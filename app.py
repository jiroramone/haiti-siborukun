import streamlit as st
import pandas as pd
import numpy as np
import re
import requests
from bs4 import BeautifulSoup

# --- 1. 基本設定 ---
st.set_page_config(page_title="配置馬券術 分析システム", layout="wide")

def to_half_width(text):
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９．', '0123456789.')
    return re.sub(r'[^\d\.]', '', text.translate(table))

def normalize_name(x):
    if pd.isna(x): return ''
    return re.sub(r'[★☆▲△◇]', '', str(x).strip().replace('　', '').replace(' ', ''))

# --- 2. データ読み込み ---
@st.cache_data
def load_data(file):
    try:
        if file.name.endswith('.xlsx'):
            df = pd.read_excel(file, engine='openpyxl')
        else:
            try: df = pd.read_csv(file, encoding='utf-8')
            except: df = pd.read_csv(file, encoding='cp932')
        
        # ヘッダー特定
        if not any(col in str(df.columns) for col in ['馬', '番', 'R', '騎']):
            for i in range(min(len(df), 10)):
                if any(x in str(df.iloc[i].values) for x in ['馬', '番', 'R']):
                    df.columns = df.iloc[i]; df = df.iloc[i+1:].reset_index(drop=True); break

        df.columns = df.columns.astype(str).str.strip()
        name_map = {
            '場所': '場名', '開催': '場名', '競馬場': '場名',
            '調教師': '厩舎', '調教師名': '厩舎', '厩舎名': '厩舎',
            '騎手名': '騎手', 'レース': 'R', '番': '正番', '馬番': '正番',
            '単オッズ': '単ｵｯｽﾞ', '単勝オッズ': '単ｵｯｽﾞ', 'オッズ': '単ｵｯｽﾞ'
        }
        df = df.rename(columns=name_map)
        
        # 数値化
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

# --- 3. 配置計算エンジン ---
def analyze_haichi(df):
    df = df.copy()
    # 4つの基礎数値を計算
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['頭数'] = max_umaban.fillna(16).astype(int)
    df['逆番'] = (df['頭数'] + 1) - df['正番']
    df['正循環'] = df['頭数'] + df['正番']
    df['逆循環'] = df['頭数'] + df['逆番']

    # 出力用列を準備
    df['タイプ'] = ''
    df['パターン'] = ''
    df['条件'] = ''
    df['スコア'] = 0.0

    # A. 青塗分析
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df.columns: continue
        group_keys = ['場名', col] if col == '騎手' else [col]
        for name, group in df.groupby(group_keys):
            if len(group) < 2 or not name: continue
            
            # 共通値の計算
            cols = ['正番', '逆番', '正循環', '逆循環']
            common = None
            for _, r in group.iterrows():
                cur_v = {int(r[c]) for c in cols if pd.notna(r[c])}
                common = cur_v if common is None else common.intersection(cur_v)
            
            if common:
                priority = 1.0 if col == '騎手' else 0.2
                c_text = ','.join(map(str, sorted(list(common))))
                df.loc[group.index, 'タイプ'] = f'★{col}青塗'
                df.loc[group.index, 'パターン'] = '青'
                df.loc[group.index, '条件'] = f'共通({c_text})'
                df.loc[group.index, 'スコア'] += 9.0 + priority

    # B. ペア分析 (A-Pパターン)
    label = list("ABCDEFGHIJKLMNOP")
    for col in ['騎手', '厩舎', '馬主']:
        if col not in df.columns: continue
        for name, group in df.groupby(['場名', col] if col=='騎手' else col):
            if len(group) < 2 or not name: continue
            sorted_idx = group.sort_values('R').index
            for i in range(len(sorted_idx)-1):
                idx1, idx2 = sorted_idx[i], sorted_idx[i+1]
                v1 = [df.at[idx1, c] for c in ['正番', '逆番', '正循環', '逆循環']]
                v2 = [df.at[idx2, c] for c in ['正番', '逆番', '正循環', '逆循環']]
                pats = [label[i*4+j] for i in range(4) for j in range(4) if v1[i] == v2[j] and v1[i] != 0]
                if pats:
                    p_str = ",".join(pats)
                    is_c = any(x in pats for x in ['C','D','G','H'])
                    for idx, other_r in [(idx1, df.at[idx2,'R']), (idx2, df.at[idx1,'R'])]:
                        df.at[idx, 'タイプ'] = '◎チャンス' if is_c else '○狙い目'
                        df.at[idx, 'パターン'] = p_str
                        df.at[idx, '条件'] = f'ペア({other_r}R)'
                        df.at[idx, 'スコア'] += 4.0 if is_c else 3.0
    
    return df

# --- 4. Webオッズ取得 ---
def fetch_odds(url):
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

# --- 5. UI構成 ---
st.title("🏇 配置馬券術 分析システム")

with st.sidebar:
    up_file = st.file_uploader("当日データをアップロード", type=['xlsx', 'csv'])
    if 'analyzed_df' in st.session_state:
        csv = st.session_state['analyzed_df'].to_csv(index=False).encode('utf-8-sig')
        st.download_button("💾 現在のデータを保存", csv, "race_result.csv")

if up_file:
    df_raw, status = load_data(up_file)
    if status == "success":
        if 'analyzed_df' not in st.session_state:
            st.session_state['analyzed_df'] = analyze_haichi(df_raw)

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
                        # オッズ更新ボタン
                        with st.expander("🌐 ネット競馬から最新オッズを取得"):
                            u_in = st.text_input("URLを貼り付け", key=f"u_{place}_{r_num}")
                            if st.button("オッズ更新実行", key=f"b_{place}_{r_num}"):
                                new_o = fetch_odds(u_in)
                                if new_o is not None:
                                    for _, row in new_o.iterrows():
                                        mask = (st.session_state['analyzed_df']['場名']==place) & (st.session_state['analyzed_df']['R']==r_num) & (st.session_state['analyzed_df']['正番']==row['正番'])
                                        st.session_state['analyzed_df'].loc[mask, '単ｵｯｽﾞ'] = row['単ｵｯｽﾞ']
                                    st.success("更新完了！")
                                    st.rerun()

                        # データ表示（全頭表示）
                        disp_df = p_df[p_df['R'] == r_num].sort_values('正番')
                        
                        # スタイル適用（スコアが高い馬に色を付ける）
                        def highlight_haichi(s):
                            return ['background-color: #ffffcc' if s.スコア > 0 else '' for _ in s]

                        st.dataframe(
                            disp_df[['正番', '馬名', '騎手', '単ｵｯｽﾞ', 'タイプ', 'パターン', '条件', 'スコア']],
                            use_container_width=True,
                            hide_index=True
                        )

                        # 推奨馬の簡易表示
                        top_horses = disp_df[disp_df['スコア'] >= 10].sort_values('スコア', ascending=False)
                        if not top_horses.empty:
                            st.info(f"🔥 配置注目馬: {', '.join(top_horses['馬名'].tolist())}")
