import streamlit as st
import pandas as pd
import numpy as np
import re
import requests
from bs4 import BeautifulSoup

st.set_page_config(page_title="配置馬券術 究極分析", layout="wide")

# ==========================================
# 1. データクリーニング
# ==========================================

def to_half_width(text):
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９．', '0123456789.')
    text = text.translate(table)
    return re.sub(r'[^\d\.]', '', text)

def normalize_name(x):
    if pd.isna(x): return ''
    name = str(x).strip().replace('　', '').replace(' ', '')
    return re.sub(r'[★☆▲△◇]', '', name)

@st.cache_data
def load_data(file):
    try:
        if file.name.endswith('.xlsx'):
            df = pd.read_excel(file, engine='openpyxl')
        else:
            try: df = pd.read_csv(file, encoding='utf-8')
            except: df = pd.read_csv(file, encoding='cp932')
        
        # ヘッダー位置自動特定
        if not any(col in str(df.columns) for col in ['馬', '番', 'R', '騎']):
            for i in range(min(len(df), 10)):
                vals = df.iloc[i].astype(str).values
                if any('馬' in v or '番' in v or 'R' in v or '騎' in v for v in vals):
                    df.columns = df.iloc[i]; df = df.iloc[i+1:].reset_index(drop=True); break

        df.columns = df.columns.astype(str).str.strip()
        name_map = {
            '場所': '場名', '開催': '場名', '競馬場': '場名',
            '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
            '騎手名': '騎手', 'レース': 'R', '番': '正番', '馬番': '正番',
            '単オッズ': '単ｵｯｽﾞ', '単勝オッズ': '単ｵｯｽﾞ', 'オッズ': '単ｵｯｽﾞ', '単勝': '単ｵｯｽﾞ'
        }
        df = df.rename(columns=name_map)
        df = df.loc[:, ~df.columns.duplicated()]

        # 必須列変換
        df['R'] = pd.to_numeric(df['R'].apply(to_half_width), errors='coerce')
        df['正番'] = pd.to_numeric(df['正番'].apply(to_half_width), errors='coerce')
        df = df.dropna(subset=['R', '正番'])
        df['R'] = df['R'].astype(int); df['正番'] = df['正番'].astype(int)

        for col in ['騎手', '厩舎', '馬主', '馬名', '場名']:
            if col in df.columns: df[col] = df[col].apply(normalize_name)
            else: df[col] = ''
        
        if '単ｵｯｽﾞ' in df.columns:
            df['単ｵｯｽﾞ'] = pd.to_numeric(df['単ｵｯｽﾞ'].apply(to_half_width), errors='coerce')
        else: df['単ｵｯｽﾞ'] = np.nan

        return df.copy(), "success"
    except Exception as e: return pd.DataFrame(), str(e)

# ==========================================
# 2. 配置計算エンジン（核心部）
# ==========================================

def get_haichi_df(df):
    """正・逆・正循環・逆循環の4つの数値を全頭計算"""
    df = df.copy()
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['使用頭数'] = max_umaban.fillna(16).astype(int)
    if '頭数' in df.columns:
        df['使用頭数'] = pd.to_numeric(df['頭数'], errors='coerce').fillna(df['使用頭数']).astype(int)

    df['逆番'] = (df['使用頭数'] + 1) - df['正番']
    df['正循環'] = df['使用頭数'] + df['正番']
    df['逆循環'] = df['使用頭数'] + df['逆番']
    return df

def get_16_pattern(r1, r2):
    """理論A〜Pの16パターン行列判定"""
    v1 = [r1['正番'], r1['逆番'], r1['正循環'], r1['逆循環']]
    v2 = [r2['正番'], r2['逆番'], r2['正循環'], r2['逆循環']]
    labels = list("ABCDEFGHIJKLMNOP")
    found = []
    for i in range(4):
        for j in range(4):
            if v1[i] == v2[j] and v1[i] != 0:
                found.append(labels[i*4 + j])
    return ",".join(found)

def run_analysis(df):
    """配置馬券ロジックの実行"""
    df = get_haichi_df(df)
    results = []
    blue_info = set()

    # 1. 青塗分析 (騎手・厩舎・馬主)
    for col in ['騎手', '厩舎', '馬主']:
        group_keys = ['場名', col] if col == '騎手' else [col]
        for name, group in df.groupby(group_keys):
            if len(group) < 2 or not name: continue
            
            # 共通値(Blue Paint)の探索
            cols = ['正番', '逆番', '正循環', '逆循環']
            common = None
            for _, r in group.iterrows():
                cur = {int(r[c]) for c in cols if pd.notna(r[c])}
                common = cur if common is None else common.intersection(cur)
            
            if common:
                priority = 1.0 if col == '騎手' else 0.2
                c_vals = ','.join(map(str, sorted(list(common))))
                for _, row in group.iterrows():
                    results.append({
                        '場名': row['場名'], 'R': row['R'], '正番': row['正番'], '馬名': row['馬名'],
                        '単ｵｯｽﾞ': row.get('単ｵｯｽﾞ'), '属性': f"{col}:{name}", 
                        'タイプ': f'★{col}青塗', 'パターン': '青', '条件': f'共通({c_vals})', 'スコア': 9.0 + priority
                    })
                    blue_info.add((row['場名'], row['R'], row['正番'], f"{col}:{name}", row.get('単ｵｯｽﾞ')))

    # 2. 青塗の隣分析 (逆転現象)
    if blue_info:
        for (place, race), group in df.groupby(['場名', 'R']):
            umaban_map = {int(r['正番']): r for _, r in group.iterrows()}
            for b_place, b_race, b_num, b_attr, b_odds in blue_info:
                if b_place == place and b_race == race:
                    for side in [b_num-1, b_num+1]:
                        if side in umaban_map:
                            s_row = umaban_map[side]
                            n_score = 9.0
                            s_odds = pd.to_numeric(s_row.get('単ｵｯｽﾞ'), errors='coerce')
                            if pd.notna(b_odds) and pd.notna(s_odds) and s_odds < b_odds: n_score += 2.0
                            results.append({
                                '場名': place, 'R': race, '正番': side, '馬名': s_row['馬名'],
                                '単ｵｯｽﾞ': s_odds, '属性': f"(隣) <{b_attr}>", 
                                'タイプ': '△青塗の隣', 'パターン': '青隣', '条件': f"#{b_num}の隣", 'スコア': n_score
                            })

    # 3. 通常ペア分析 (理論A-P)
    for col in ['騎手', '厩舎', '馬主']:
        for name, group in df.groupby(['場名', col] if col=='騎手' else col):
            if len(group) < 2 or not name: continue
            sorted_group = group.sort_values('R').to_dict('records')
            for i in range(len(sorted_group)-1):
                r1, r2 = sorted_group[i], sorted_group[i+1]
                pat = get_16_pattern(r1, r2)
                if pat:
                    is_chanse = any(x in pat for x in ['C','D','G','H'])
                    score = 4.0 if is_chanse else 3.0
                    for row in [r1, r2]:
                        results.append({
                            '場名': row['場名'], 'R': row['R'], '正番': row['正番'], '馬名': row['馬名'],
                            '単ｵｯｽﾞ': row.get('単ｵｯｽﾞ'), '属性': f"{col}:{name}", 
                            'タイプ': '◎チャンス' if is_chanse else '○狙い目', 
                            'パターン': pat, '条件': f'ペア({r1["R"]}R-{r2["R"]}R)', 'スコア': score
                        })

    if not results: return pd.DataFrame()
    res_df = pd.DataFrame(results)
    agg_funcs = {'単ｵｯｽﾞ': 'min', '属性': lambda x: ' + '.join(sorted(set(x))), 'タイプ': lambda x: ' / '.join(sorted(set(x))), 'パターン': lambda x: ','.join(sorted(set(x))), '条件': lambda x: ' / '.join(sorted(set(x))), 'スコア': 'sum', '正番': 'first'}
    return res_df.groupby(['場名', 'R', '馬名'], as_index=False).agg(agg_funcs)

# ==========================================
# 3. 総合判定（推奨マーク）
# ==========================================

def apply_ranking(df):
    if df.empty: return df
    df = df.copy()
    if '着順' not in df.columns: df['着順'] = np.nan
    df['着順'] = pd.to_numeric(df['着順'], errors='coerce')
    
    # 的中トレンドの学習
    hit_patterns = set()
    if not df[df['着順']<=3].empty:
        p_str = ','.join(df[df['着順']<=3]['パターン'].dropna().astype(str))
        hit_patterns = set(p_str.split(','))

    def get_recommendation(row):
        score = row.get('スコア', 0)
        odds = pd.to_numeric(row.get('単ｵｯｽﾞ'), errors='coerce')
        pats = str(row.get('パターン', '')).split(',')
        
        bonus = 0.0
        if any(p in hit_patterns and len(p)==1 for p in pats): bonus += 4.0 # トレンド加点
        if pd.notna(odds) and odds > 49.9: bonus -= 30.0 # 大穴除外
        
        total = score + bonus
        row['総合スコア'] = total
        
        if total >= 15: return "👑 盤石の軸"
        if total >= 12: return "✨ 推奨軸"
        if total >= 10: return "🔥 激熱相手"
        if '青' in pats: return "▲ 青塗穴"
        return "△ 紐"

    df['推奨買い目'] = df.apply(get_recommendation, axis=1)
    return df

# ==========================================
# 4. Web取得
# ==========================================

def fetch_netkeiba_odds(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = 'euc-jp'
        soup = BeautifulSoup(resp.content, 'html.parser')
        rows = soup.select('tr.HorseList')
        data = []
        for r in rows:
            u = r.select_one('td[class*="Umaban"]')
            o = r.select_one('td[class*="Popular"]')
            if u:
                u_n = u.get_text(strip=True)
                o_v = re.sub(r'\(.*?\)', '', o.get_text(strip=True)) if o else 'nan'
                try: dv = float(o_v)
                except: dv = np.nan
                data.append({'正番': int(u_n), '単ｵｯｽﾞ': dv})
        return pd.DataFrame(data) if data else None
    except: return None

# ==========================================
# 5. UI
# ==========================================

st.title("🏇 配置馬券術 究極分析システム")

with st.sidebar:
    up_file = st.file_uploader("出馬表アップロード", type=['xlsx', 'csv'])
    if 'analyzed_df' in st.session_state:
        st.download_button("💾 保存", st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig'), "race_result.csv")

if up_file:
    df_raw, status = load_data(up_file)
    if status == "success":
        if 'analyzed_df' not in st.session_state:
            with st.spinner('配置ロジック計算中...'):
                res = run_analysis(df_raw)
                st.session_state['analyzed_df'] = apply_ranking(res)

        full_df = st.session_state['analyzed_df']
        places = sorted(full_df['場名'].unique())
        p_tabs = st.tabs(places)
        
        for p_tab, place in zip(p_tabs, places):
            with p_tab:
                p_df = full_df[full_df['場名'] == place]
                r_list = sorted(p_df['R'].unique())
                r_tabs = st.tabs([f"{r}R" for r in r_list])
                for r_tab, r_num in zip(r_tabs, r_list):
                    with r_tab:
                        # --- オッズ更新 ---
                        with st.expander("🌐 最新オッズ取得"):
                            u_in = st.text_input("URL", key=f"u_{place}_{r_num}")
                            if st.button("更新", key=f"b_{place}_{r_num}"):
                                new_o = fetch_netkeiba_odds(u_in)
                                if new_o is not None:
                                    for _, row in new_o.iterrows():
                                        mask = (st.session_state['analyzed_df']['場名']==place) & (st.session_state['analyzed_df']['R']==r_num) & (st.session_state['analyzed_df']['正番']==row['正番'])
                                        st.session_state['analyzed_df'].loc[mask, '単ｵｯｽﾞ'] = row['単ｵｯｽﾞ']
                                    st.session_state['analyzed_df'] = apply_ranking(st.session_state['analyzed_df'])
                                    st.rerun()

                        # --- 表示 ---
                        disp = p_df[p_df['R'] == r_num].sort_values('総合スコア', ascending=False)
                        st.dataframe(disp[['正番', '馬名', '単ｵｯｽﾞ', 'タイプ', 'パターン', '条件', '推奨買い目']], use_container_width=True, hide_index=True)
