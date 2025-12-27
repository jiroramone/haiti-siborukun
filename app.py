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
    # 名前、レース番号、記号の混在を洗浄（例: "高杉吏麒,2R" -> "高杉吏麒"）
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
        
        # ヘッダー自動特定
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
            '正循': '正循環', '逆循': '逆循環'
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

# --- 3. 配置計算エンジン (全レース共通ロジック) ---
def analyze_haichi(df):
    df = df.copy()
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
    df['条件_list'] = [[] for _ in range(len(df))]
    df['スコア'] = 0.0

    idx_map = {(row['場名'], row['R'], row['正番']): idx for idx, row in df.iterrows()}

    # A. 青塗 (当日全レース共通値)
    blue_info = []
    for col in ['騎手', '厩舎', '馬主']:
        group_keys = ['場名', col] if col == '騎手' else [col]
        for name, group in df.groupby(group_keys):
            if len(group) < 2 or not name: continue
            all_sets = [{r['正番'], r['逆番'], r['正循環'], r['逆循環']} for _, r in group.iterrows()]
            common = set.intersection(*all_sets)
            if common:
                priority = 1.0 if col == '騎手' else 0.2
                val_str = ','.join(map(str, sorted(list(common))))
                for _, row in group.iterrows():
                    idx = idx_map.get((row['場名'], row['R'], row['正番']))
                    if idx is not None:
                        df.at[idx, 'タイプ_list'].append(f'★{col}青塗')
                        df.at[idx, '属性_list'].append(f'{col}:{name}')
                        df.at[idx, 'パターン_list'].append('青塗')
                        df.at[idx, '条件_list'].append(f'全共通({val_str})')
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
                    df.at[idx, '条件_list'].append(f"#{b['正番']}の隣")
                    df.at[idx, 'スコア'] += n_score

    # C. ペア分析
    pair_labels = list("ABCDEFGHIJKLMNOP")
    for col in ['騎手', '厩舎', '馬主']:
        for name, group in df.groupby(['場名', col] if col=='騎手' else col):
            if len(group) < 2 or not name: continue
            rows = group.sort_values('R').to_dict('records')
            for i in range(len(rows)-1):
                r1, r2 = rows[i], rows[i+1]
                v1 = [r1[c] for c in ['正番', '逆番', '正循環', '逆循環']]
                v2 = [r2[c] for c in ['正番', '逆番', '正循環', '逆循環']]
                pats = [pair_labels[x*4+y] for x in range(4) for y in range(4) if v1[x]==v2[y] and v1[x]!=0]
                if pats:
                    p_str = "".join(pats); is_c = any(x in pats for x in ['C','D','G','H'])
                    for r_data, partner_R in [(r1, r2['R']), (r2, r1['R'])]:
                        idx = idx_map.get((r_data['場名'], r_data['R'], r_data['正番']))
                        if idx is not None:
                            df.at[idx, 'タイプ_list'].append('◎チャンス' if is_c else '○狙い目')
                            df.at[idx, '属性_list'].append(f'{col}:{name}')
                            df.at[idx, 'パターン_list'].append(p_str)
                            df.at[idx, '条件_list'].append(f"ペア({partner_R}R)")
                            df.at[idx, 'スコア'] += 4.0 if is_c else 3.0

    df['タイプ'] = df['タイプ_list'].apply(lambda x: ' / '.join(x))
    df['属性'] = df['属性_list'].apply(lambda x: ' / '.join(list(set(x))))
    df['パターン'] = df['パターン_list'].apply(lambda x: ','.join(x))
    df['条件'] = df['条件_list'].apply(lambda x: ' '.join(x))
    return df

# --- 4. 判定ロジック ---
def apply_ranking_logic(df_in):
    if df_in.empty: return df_in
    df = df_in.copy()
    df['着順'] = pd.to_numeric(df['着順'], errors='coerce')
    
    # 的中パターンの抽出
    fuku_df = df[df['着順'] <= 3]
    hit_pats_list = []
    for p in fuku_df['パターン'].dropna():
        if p: hit_pats_list.extend(str(p).split(','))
    hit_patterns = set(hit_pats_list)

    def get_rec(row):
        score = row.get('スコア', 0)
        pats = str(row.get('パターン', '')).split(',')
        bonus = 4.0 if any(p in hit_patterns and len(p)==1 for p in pats) else 0.0
        odds = pd.to_numeric(row.get('単ｵｯｽﾞ'), errors='coerce')
        if odds > 49.9: score -= 30.0
        total = score + bonus
        if total >= 15: return "👑 盤石の軸"
        if total >= 12: return "✨ 推奨軸"
        if total >= 10: return "🔥 激熱相手"
        return "▲ 青塗穴" if '青' in str(row['タイプ']) else "△ 紐"

    df['推奨買い目'] = df.apply(get_rec, axis=1)
    df['傾向加点'] = df.apply(lambda r: 4.0 if any(p in hit_patterns and len(p)==1 for p in str(r['パターン']).split(',')) else 0.0, axis=1)
    df['総合スコア'] = df['スコア'] + df['傾向加点']
    return df

# --- 5. UI ---
st.title("🏇 配置馬券術 注目馬シボリ君")

with st.sidebar:
    up_file = st.file_uploader("データアップロード", type=['xlsx', 'csv'])
    if 'analyzed_df' in st.session_state:
        st.download_button("💾 全頭保存", st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig'), "race_result.csv")

if up_file:
    df_raw, status = load_data(up_file)
    if status == "success":
        if 'analyzed_df' not in st.session_state:
            st.session_state['analyzed_df'] = apply_ranking_logic(analyze_haichi(df_raw))
        
        full_df = st.session_state['analyzed_df']
        df_results = full_df[full_df['着順'].notna()].copy()
        df_fuku = df_results[df_results['着順'] <= 3]
        
        st.subheader("📊 本日の的中統計")
        c1, c2, c3 = st.columns(3)
        with c1: st.metric("消化レース", len(df_results['R'].unique()))
        with c2: 
            rate = len(df_fuku)/len(df_results)*100 if len(df_results)>0 else 0
            st.metric("注目馬 複勝率", f"{rate:.1f}%")
        with c3: st.metric("的中数", f"{len(df_fuku)} 頭")

        # --- 特設: 推奨馬枠 ---
        st.divider()
        st.subheader("👑 本日の特選推奨馬 (未確定レース)")
        future_recs = full_df[(full_df['着順'].isna()) & (full_df['総合スコア'] >= 10)].sort_values(['場名','R','総合スコア'], ascending=[True, True, False])
        if future_recs.empty:
            st.write("現在、推奨馬はいません。")
        else:
            st.dataframe(future_recs[['場名','R','正番','馬名','単ｵｯｽﾞ','タイプ','属性','総合スコア','推奨買い目']], use_container_width=True, hide_index=True)

        # --- メイン: 結果入力エリア ---
        st.divider()
        st.subheader("📝 結果入力 & 注目馬リスト")
        places = sorted(full_df['場名'].unique())
        
        with st.form("result_form"):
            p_tabs = st.tabs(places)
            edited_dfs = []
            for p_tab, place in zip(p_tabs, places):
                with p_tab:
                    p_df = full_df[full_df['場名'] == place]
                    r_nums = sorted(p_df['R'].unique())
                    r_tabs = st.tabs([f"{r}R" for r in r_nums])
                    for r_tab, r_num in zip(r_tabs, r_nums):
                        with r_tab:
                            race_full = p_df[p_df['R'] == r_num].sort_values('正番')
                            disp = race_full[race_full['スコア'] > 0].copy()
                            
                            if disp.empty:
                                st.caption("配置該当なし")
                                edited_dfs.append(race_full)
                            else:
                                # 表示列に「属性」を追加
                                ed = st.data_editor(disp[['正番','馬名','単ｵｯｽﾞ','属性','タイプ','パターン','総合スコア','着順','推奨買い目']], 
                                                   disabled=['正番','馬名','単ｵｯｽﾞ','属性','タイプ','パターン','総合スコア','推奨買い目'], 
                                                   hide_index=True, use_container_width=True, key=f"ed_{place}_{r_num}")
                                updated_race = race_full.copy()
                                for _, row in ed.iterrows():
                                    updated_race.loc[updated_race['正番'] == row['正番'], '着順'] = row['着順']
                                edited_dfs.append(updated_race)

            if st.form_submit_button("🔄 入力を確定して更新・再計算"):
                combined = pd.concat(edited_dfs, ignore_index=True)
                st.session_state['analyzed_df'] = apply_ranking_logic(combined)
                st.rerun()

        # --- 最下部: 的中パターン分析グラフ ---
        if not df_fuku.empty:
            st.divider()
            st.write("### 📈 的中パターンの傾向分析")
            all_p_hits = []
            for p in df_fuku['パターン'].dropna():
                if p: all_p_hits.extend(str(p).split(','))
            
            if all_p_hits:
                df_plot = pd.Series(all_p_hits).value_counts().reset_index()
                df_plot.columns = ['パターン', '的中数']
                fig = px.pie(df_plot, values='的中数', names='パターン', title='的中パターンの内訳', hole=0.4)
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("集計可能な的中パターンデータがまだありません。")
