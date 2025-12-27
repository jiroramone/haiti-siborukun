import streamlit as st
import pandas as pd
import numpy as np
import re

# --- 1. 基本設定 ---
st.set_page_config(page_title="配置馬券術 分析システム", layout="wide")

def to_half_width(text):
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９．', '0123456789.')
    return re.sub(r'[^\d\.]', '', text.translate(table))

def normalize_name(x):
    if pd.isna(x): return ''
    return re.sub(r'[★☆▲△◇$]', '', str(x).strip().replace('　', '').replace(' ', ''))

# --- 2. データ読み込み ---
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
                if any(x in str(df.iloc[i].values) for x in ['馬', '番', 'R']):
                    df.columns = df.iloc[i]; df = df.iloc[i+1:].reset_index(drop=True); break

        df.columns = df.columns.astype(str).str.strip()
        # 列名の名寄せ（12/27ファイルにある「正循」「逆循」に完全対応）
        name_map = {
            '場所': '場名', '開催': '場名', '競馬場': '場名',
            '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
            '騎手名': '騎手', 'レース': 'R', 'Ｒ': 'R', '番': '正番', '馬番': '正番',
            '単オッズ': '単ｵｯｽﾞ', '単勝オッズ': '単ｵｯｽﾞ', 'オッズ': '単ｵｯｽﾞ',
            '正循': '正循環', '逆循': '逆循環'
        }
        df = df.rename(columns=name_map)
        
        # 数値化とクリーニング
        df['R'] = pd.to_numeric(df['R'].apply(to_half_width), errors='coerce')
        df['正番'] = pd.to_numeric(df['正番'].apply(to_half_width), errors='coerce')
        df = df.dropna(subset=['R', '正番'])
        df['R'] = df['R'].astype(int); df['正番'] = df['正番'].astype(int)

        for col in ['騎手', '厩舎', '馬主', '馬名', '場名']:
            if col in df.columns: df[col] = df[col].apply(normalize_name)
        
        if '単ｵｯｽﾞ' in df.columns:
            df['単ｵｯｽﾞ'] = pd.to_numeric(df['単ｵｯｽﾞ'].apply(to_half_width), errors='coerce')
        
        return df.copy(), "success"
    except Exception as e: return pd.DataFrame(), str(e)

# --- 3. 配置計算エンジン (全レース共通値ロジック) ---
def analyze_haichi(df):
    df = df.copy()
    
    # 基礎数値計算（もしファイルになければ計算、あればそれを使う）
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    if '逆番' not in df.columns or df['逆番'].isna().all():
        df['逆番'] = (max_umaban + 1) - df['正番']
    if '正循環' not in df.columns or df['正循環'].isna().all():
        df['正循環'] = max_umaban + df['正番']
    if '逆循環' not in df.columns or df['逆循環'].isna().all():
        df['逆循環'] = max_umaban + df['逆番']

    # 各値を整数化
    for c in ['正番', '逆番', '正循環', '逆循環']:
        df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0).astype(int)

    # 初期化
    df['タイプ_list'] = [[] for _ in range(len(df))]
    df['パターン_list'] = [[] for _ in range(len(df))]
    df['条件_list'] = [[] for _ in range(len(df))]
    df['スコア'] = 0.0

    # マッピング用
    idx_map = {}
    for idx, row in df.iterrows():
        idx_map[(row['場名'], row['R'], row['正番'])] = idx

    # --- A. 青塗分析 (全レース共通値ロジック) ---
    blue_info = [] # 隣判定用
    
    for col in ['騎手', '厩舎', '馬主']:
        # 騎手は場名ごと、厩舎・馬主は全体でグループ化
        group_keys = ['場名', col] if col == '騎手' else [col]
        for name, group in df.groupby(group_keys):
            if len(group) < 2 or not name: continue
            
            # そのグループの「全レース」の配置セットを取得
            all_sets = []
            for _, row in group.iterrows():
                all_sets.append({row['正番'], row['逆番'], row['正循環'], row['逆循環']})
            
            # ★すべてのセットに共通する値を抽出 (全レース共通値)
            common = set.intersection(*all_sets)
            
            if common:
                priority = 1.0 if col == '騎手' else 0.2
                val_str = ','.join(map(str, sorted(list(common))))
                for _, row in group.iterrows():
                    idx = idx_map.get((row['場名'], row['R'], row['正番']))
                    if idx is not None:
                        df.at[idx, 'タイプ_list'].append(f'★{col}青塗')
                        df.at[idx, 'パターン_list'].append('青塗')
                        df.at[idx, '条件_list'].append(f'全共通({val_str})')
                        df.at[idx, 'スコア'] += 9.0 + priority
                        blue_info.append({'場名':row['場名'], 'R':row['R'], '正番':row['正番'], '属性':f"{col}:{name}", '単ｵｯｽﾞ':row['単ｵｯｽﾞ']})

    # --- B. 青塗の隣 ---
    for b in blue_info:
        for t_num in [b['正番']-1, b['正番']+1]:
            key = (b['場名'], b['R'], t_num)
            if key in idx_map:
                idx = idx_map[key]; n_score = 9.0; is_rev = False
                if pd.notna(b['単ｵｯｽﾞ']) and pd.notna(df.at[idx, '単ｵｯｽﾞ']):
                    if df.at[idx, '単ｵｯｽﾞ'] < b['単ｵｯｽﾞ']: n_score += 2.0; is_rev = True
                if not any('青塗隣' in x for x in df.at[idx, 'タイプ_list']):
                    df.at[idx, 'タイプ_list'].append('△青塗隣' + ('(逆転)' if is_rev else ''))
                    df.at[idx, 'パターン_list'].append('青隣')
                    df.at[idx, '条件_list'].append(f"#{b['正番']}の隣")
                    df.at[idx, 'スコア'] += n_score

    # --- C. ペア分析 (通常ペア) ---
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
                            df.at[idx, 'パターン_list'].append(p_str)
                            df.at[idx, '条件_list'].append(f"ペア({partner_R}R)")
                            df.at[idx, 'スコア'] += 4.0 if is_c else 3.0

    df['タイプ'] = df['タイプ_list'].apply(lambda x: ' / '.join(x))
    df['パターン'] = df['パターン_list'].apply(lambda x: ','.join(x))
    df['条件'] = df['条件_list'].apply(lambda x: ' '.join(x))
    return df

# --- 4. 判定ロジック (保存・表示用) ---
def apply_ranking_logic(df):
    df = df.copy()
    if '着順' not in df.columns: df['着順'] = np.nan
    df['着順'] = pd.to_numeric(df['着順'], errors='coerce')
    hit_pats = set(','.join(df[df['着順']<=3]['パターン'].dropna().astype(str)).split(',')) if not df[df['着順']<=3].empty else set()
    def get_rec(row):
        total = row['スコア'] + (4.0 if any(p in hit_pats and len(p)==1 for p in str(row['パターン']).split(',')) else 0.0)
        if total >= 15: return "👑 盤石の軸"
        if total >= 12: return "✨ 推奨軸"
        if total >= 10: return "🔥 激熱相手"
        return "▲ 青塗穴" if '青' in str(row['パターン']) else "△ 紐"
    df['推奨買い目'] = df.apply(get_rec, axis=1)
    return df

# --- 5. UI ---
st.title("🏇 配置馬券術 分析システム (12/27修正版)")

with st.sidebar:
    up_file = st.file_uploader("当日データをアップロード", type=['xlsx', 'csv'])
    if 'analyzed_df' in st.session_state:
        st.download_button("💾 データを保存", st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig'), "race_result.csv")

if up_file:
    df_raw, status = load_data(up_file)
    if status == "success":
        st.session_state['analyzed_df'] = apply_ranking_logic(analyze_haichi(df_raw))
        full_df = st.session_state['analyzed_df']
        places = sorted(full_df['場名'].unique())
        p_tabs = st.tabs(places)
        for p_tab, place in zip(p_tabs, places):
            with p_tab:
                p_df = full_df[full_df['場名'] == place]
                r_tabs = st.tabs([f"{r}R" for r in sorted(p_df['R'].unique())])
                for r_tab, r_num in zip(r_tabs, sorted(p_df['R'].unique())):
                    with r_tab:
                        disp = p_df[p_df['R'] == r_num].sort_values('正番')
                        def style_row(row):
                            if row['スコア'] >= 10: return ['background-color: #ffcccc'] * len(row)
                            if '青' in str(row['タイプ']): return ['background-color: #e6f3ff'] * len(row)
                            return [''] * len(row)
                        st.dataframe(disp[['正番', '馬名', '騎手', '単ｵｯｽﾞ', 'タイプ', 'パターン', '条件', 'スコア', '推奨買い目']].style.apply(style_row, axis=1), use_container_width=True, hide_index=True)
