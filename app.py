import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px

# --- 1. 基本設定 ---
st.set_page_config(page_title="配置馬券術 分析システム", layout="wide")

def to_half_width(text):
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９．', '0123456789.')
    return re.sub(r'[^\d\.]', '', text.translate(table))

def normalize_name(x):
    if pd.isna(x): return ''
    s = str(x).strip().replace('　', '').replace(' ', '')
    s = re.split(r'[,(（/]', s)[0]
    return re.sub(r'[★☆▲△◇$*]', '', s)

# --- 2. データ読み込み ---
@st.cache_data
def load_data(file):
    try:
        if file.name.endswith('.xlsx'):
            df = pd.read_excel(file, engine='openpyxl')
        else:
            try: df = pd.read_csv(file, encoding='utf-8')
            except: df = pd.read_csv(file, encoding='cp932')
        
        if not any(col in str(df.columns) for col in ['馬', '番', 'R', '騎']):
            for i in range(min(len(df), 10)):
                if any(x in str(df.iloc[i].values) for x in ['馬', '番', 'R']):
                    df.columns = df.iloc[i]; df = df.iloc[i+1:].reset_index(drop=True); break

        df.columns = df.columns.astype(str).str.strip()
        name_map = {
            '場所': '場名', '開催': '場名', '競馬場': '場名',
            '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
            '騎手名': '騎手', 'レース': 'R', 'Ｒ': 'R', '番': '正番', '馬番': '正番',
            '単オッズ': '単ｵｯｽﾞ', '単勝オッズ': '単ｵｯｽﾞ', 'オッズ': '単ｵｯｽﾞ',
            '正循': '正循環', '逆循': '逆循環', '着': '着順'
        }
        df = df.rename(columns=name_map)
        ensure_cols = ['R', '場名', '馬名', '正番', '騎手', '厩舎', '馬主', '単ｵｯｽﾞ', '着順']
        for col in ensure_cols:
            if col not in df.columns: df[col] = np.nan

        df['R'] = pd.to_numeric(df['R'].apply(to_half_width), errors='coerce')
        df['正番'] = pd.to_numeric(df['正番'].apply(to_half_width), errors='coerce')
        df = df.dropna(subset=['R', '正番'])
        df['R'] = df['R'].astype(int); df['正番'] = df['正番'].astype(int)
        for col in ['騎手', '厩舎', '馬主', '馬名', '場名']:
            df[col] = df[col].apply(normalize_name)
        df['単ｵｯｽﾞ'] = pd.to_numeric(df['単ｵｯｽﾞ'].apply(to_half_width), errors='coerce')
        return df.copy(), "success"
    except Exception as e: return pd.DataFrame(), str(e)

# --- 3. 配置計算エンジン ---
def analyze_haichi(df_curr, df_prev=None):
    df = df_curr.copy()
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['頭数'] = max_umaban.fillna(16).astype(int)
    df['逆番'] = (df['頭数'] + 1) - df['正番']
    df['正循環'] = df['頭数'] + df['正番']
    df['逆循環'] = df['頭数'] + df['逆番']
    for c in ['正番', '逆番', '正循環', '逆循環']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)

    df['タイプ_list'] = [[] for _ in range(len(df))]
    df['属性_list'] = [[] for _ in range(len(df))]
    df['パターン_list'] = [[] for _ in range(len(df))]
    df['スコア'] = 0.0
    idx_map = {(row['場名'], row['R'], row['正番']): idx for idx, row in df.iterrows()}

    # A. 青塗 (当日内全鞍共通)
    blue_info = []
    for col in ['騎手', '厩舎', '馬主']:
        group_keys = ['場名', col] if col == '騎手' else [col]
        for name, group in df.groupby(group_keys):
            if len(group) < 2 or not name: continue
            all_sets = [{r['正番'], r['逆番'], r['正循環'], r['逆循環']} for _, r in group.iterrows()]
            common = set.intersection(*all_sets)
            if common:
                priority = 1.0 if col == '騎手' else 0.2
                for _, row in group.iterrows():
                    idx = idx_map.get((row['場名'], row['R'], row['正番']))
                    if idx is not None:
                        df.at[idx, 'タイプ_list'].append(f'★{col}青塗')
                        df.at[idx, '属性_list'].append(f'{col}:{name}')
                        df.at[idx, 'パターン_list'].append('青塗')
                        df.at[idx, 'スコア'] += 9.0 + priority
                        blue_info.append({'場名':row['場名'], 'R':row['R'], '正番':row['正番'], '属性':f"{col}:{name}", '単ｵｯｽﾞ':row['単ｵｯｽﾞ']})

    # B. 青塗の隣
    for b in blue_info:
        for t_num in [b['正番']-1, b['正番']+1]:
            key = (b['場名'], b['R'], t_num)
            if key in idx_map:
                idx = idx_map[key]; n_score = 9.0; is_rev = False
                if pd.notna(b['単ｵｯｽﾞ']) and pd.notna(df.at[idx, '単ｵｯｽﾞ']):
                    if df.at[idx, '単ｵｯｽﾞ'] < b['単ｵｯｽﾞ']: n_score += 2.0; is_rev = True
                if not any('青塗隣' in str(x) for x in df.at[idx, 'タイプ_list']):
                    df.at[idx, 'タイプ_list'].append('△青塗隣' + ('(逆転)' if is_rev else ''))
                    df.at[idx, '属性_list'].append(f'隣:{b["属性"]}')
                    df.at[idx, 'パターン_list'].append('青隣')
                    df.at[idx, 'スコア'] += n_score

    # C. ペア
    pair_labels = list("ABCDEFGHIJKLMNOP")
    for col in ['騎手', '厩舎', '馬主']:
        for name, group in df.groupby(['場名', col] if col=='騎手' else col):
            if len(group) < 2 or not name: continue
            rows = group.sort_values('R').to_dict('records')
            for i in range(len(rows)-1):
                r1, r2 = rows[i], rows[i+1]
                v1, v2 = [r1[c] for c in ['正番','逆番','正循環','逆循環']], [r2[c] for c in ['正番','逆番','正循環','逆循環']]
                pats = [pair_labels[x*4+y] for x in range(4) for y in range(4) if v1[x]==v2[y] and v1[x]!=0]
                if pats:
                    p_str = "".join(pats); is_c = any(x in pats for x in ['C','D','G','H'])
                    for r_data, partner_R in [(r1, r2['R']), (r2, r1['R'])]:
                        idx = idx_map.get((r_data['場名'], r_data['R'], r_data['正番']))
                        if idx is not None:
                            df.at[idx, 'タイプ_list'].append('◎チャンス' if is_c else '○狙い目')
                            df.at[idx, '属性_list'].append(f'{col}:{name}')
                            df.at[idx, 'パターン_list'].append(p_str)
                            df.at[idx, 'スコア'] += 4.0 if is_c else 3.0

    # D. 前日リンク
    if df_prev is not None and not df_prev.empty:
        for idx, row in df.iterrows():
            prev_match = df_prev[(df_prev['場名'] == row['場名']) & (df_prev['R'] == row['R']) & (df_prev['騎手'] == row['騎手'])]
            for _, p_row in prev_match.iterrows():
                if {row['正番'],row['逆番'],row['正循環'],row['逆循環']}.intersection({p_row['正番'],p_row['逆番'],p_row['正循環'],p_row['逆循環']}):
                    df.at[idx, 'タイプ_list'].append('★前日同配置'); df.at[idx, '属性_list'].append(f'前日:騎手:{row["騎手"]}'); df.at[idx, 'パターン_list'].append('前日'); df.at[idx, 'スコア'] += 8.3

    df['タイプ'] = df['タイプ_list'].apply(lambda x: ' / '.join(x) if isinstance(x, list) else x)
    df['属性'] = df['属性_list'].apply(lambda x: ' / '.join(list(set(x))) if isinstance(x, list) else x)
    df['パターン'] = df['パターン_list'].apply(lambda x: ','.join(x) if isinstance(x, list) else x)
    return df

# --- 4. 判定ロジック (エネルギー属性明示版) ---
def apply_ranking_logic(df_in):
    if df_in.empty: return df_in
    df = df_in.copy()
    df['着順'] = pd.to_numeric(df['着順'], errors='coerce')
    
    # 3着以内に入った属性を特定
    hit_results = df[df['着順'] <= 3]
    hit_attrs = set()
    for _, row in hit_results.iterrows():
        raw_attrs = str(row.get('属性', '')).split(' / ')
        for a in raw_attrs:
            # 属性（騎手:〇〇など）をそのままブラックリストに登録
            clean_a = a.replace('隣:', '').replace('前日:', '')
            hit_attrs.add(clean_a)

    hit_patterns = set([p for pats in hit_results['パターン'].dropna() for p in str(pats).split(',') if p])

    def get_final_metrics(row):
        score = row.get('スコア', 0)
        p_list = str(row.get('パターン', '')).split(',')
        trend_bonus = 4.0 if any(p in hit_patterns and len(p)==1 for p in p_list) else 0.0
        
        # エネルギー消費減点判定 & 属性特定
        consumption_penalty = 0.0
        penalty_reasons = []
        row_attrs = str(row.get('属性', '')).split(' / ')
        for ra in row_attrs:
            clean_ra = ra.replace('隣:', '').replace('前日:', '')
            if clean_ra in hit_attrs:
                consumption_penalty = -3.0
                # 属性名（騎手、厩舎、馬主）を抽出
                attr_type = clean_ra.split(':')[0] if ':' in clean_ra else "本人"
                penalty_reasons.append(attr_type)
        
        penalty_msg = f"⚠️好走済({','.join(set(penalty_reasons))})(-3)" if penalty_reasons else ""
        
        odds_penalty = -30.0 if pd.to_numeric(row.get('単ｵｯｽﾞ'), errors='coerce') > 49.9 else 0.0
        total = score + trend_bonus + consumption_penalty + odds_penalty
        
        if total >= 15: rec = "👑 盤石の軸"
        elif total >= 12: rec = "✨ 推奨軸"
        elif total >= 10: rec = "🔥 激熱相手"
        else: rec = "▲ 配置注目" if score > 0 else ""
            
        return pd.Series([total, trend_bonus, consumption_penalty, rec, penalty_msg])

    df[['総合スコア', '傾向加点', '消費減点', '推奨買い目', 'エネルギー状態']] = df.apply(get_final_metrics, axis=1)
    return df

# --- 5. UI ---
st.title("🏇 配置馬券術 注目馬シボリ君")

with st.sidebar:
    st.header("📂 読み込み")
    up_curr = st.file_uploader("当日データ", type=['xlsx', 'csv'], key="curr")
    up_prev = st.file_uploader("前日データ", type=['xlsx', 'csv'], key="prev")
    st.divider()
    if 'analyzed_df' in st.session_state and not st.session_state['analyzed_df'].empty:
        csv = st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 経過を保存", csv, "race_progress.csv")

if up_curr:
    df_raw, status = load_data(up_curr)
    df_p_raw, _ = load_data(up_prev) if up_prev else (None, None)
    
    if status == "success":
        if 'analyzed_df' not in st.session_state:
            st.session_state['analyzed_df'] = apply_ranking_logic(analyze_haichi(df_raw, df_p_raw))
        
        full_df = st.session_state['analyzed_df']

        # ① 結果入力エリア
        st.subheader("📝 結果入力 (配置馬のみ)")
        places = sorted(full_df['場名'].unique())
        with st.form("result_form"):
            p_tabs = st.tabs(places)
            edited_dfs = []
            for p_tab, place in zip(p_tabs, places):
                with p_tab:
                    p_df = full_df[full_df['場名'] == place]
                    r_tabs = st.tabs([f"{r}R" for r in sorted(p_df['R'].unique())])
                    for r_tab, r_num in zip(r_tabs, sorted(p_df['R'].unique())):
                        with r_tab:
                            race_full = p_df[p_df['R'] == r_num].sort_values('正番')
                            disp = race_full[race_full['スコア'] > 0].copy()
                            if disp.empty: st.caption("配置該当なし")
                            else:
                                ed = st.data_editor(disp[['正番','馬名','単ｵｯｽﾞ','属性','エネルギー状態','総合スコア','着順','推奨買い目']], hide_index=True, use_container_width=True, key=f"ed_{place}_{r_num}")
                                updated_race = race_full.copy()
                                for _, row in ed.iterrows(): updated_race.loc[updated_race['正番'] == row['正番'], '着順'] = row['着順']
                                edited_dfs.append(updated_race)
            if st.form_submit_button("🔄 確定して更新"):
                combined = pd.concat(edited_dfs, ignore_index=True)
                st.session_state['analyzed_df'] = apply_ranking_logic(combined); st.rerun()

        # ② 推奨馬リスト
        st.divider()
        st.subheader("👑 特選推奨馬")
        future_df = full_df[(full_df['着順'].isna()) & (full_df['総合スコア'] >= 10)]
        if not future_df.empty:
            f_p_tabs = st.tabs(sorted(future_df['場名'].unique()))
            for f_p_tab, place in zip(f_p_tabs, sorted(future_df['場名'].unique())):
                with f_p_tab:
                    p_future = future_df[future_df['場名'] == place]
                    f_r_tabs = st.tabs([f"{r}R" for r in sorted(p_future['R'].unique())])
                    for f_r_tab, r_num in zip(f_r_tabs, sorted(p_future['R'].unique())):
                        with f_r_tab:
                            st.dataframe(p_future[p_future['R'] == r_num].sort_values('総合スコア', ascending=False)[['正番','馬名','単ｵｯｽﾞ','属性','エネルギー状態','総合スコア','推奨買い目']], use_container_width=True, hide_index=True)

        # ③ 統計 ＆ 分析グラフ
        st.divider()
        st.subheader("📈 的中傾向 (会場別)")
        df_results = full_df[full_df['着順'].notna()].copy()
        s_tabs = st.tabs(["合計"] + sorted(full_df['場名'].unique()))
        for s_tab, s_place in zip(s_tabs, ["合計"] + sorted(full_df['場名'].unique())):
            with s_tab:
                df_s = df_results if s_place == "合計" else df_results[df_results['場名'] == s_place]
                df_fuku = df_s[df_s['着順'] <= 3]
                if df_s.empty: st.info("データなし")
                else:
                    c_m, c_c = st.columns([1, 2])
                    with c_m:
                        st.metric("消化レース", len(df_s['R'].unique()))
                        st.metric("注目馬 複勝率", f"{len(df_fuku)/len(df_s)*100 if len(df_s)>0 else 0:.1f}%")
                        st.metric("的中数", f"{len(df_fuku)} 頭")
                    with c_c:
                        all_p = [p for pats in df_fuku['パターン'] for p in str(pats).split(',') if p]
                        if all_p: st.plotly_chart(px.pie(pd.Series(all_p).value_counts().reset_index(), values='count', names='index', title=f'【{s_place}】的中パターン', hole=0.4), use_container_width=True)
