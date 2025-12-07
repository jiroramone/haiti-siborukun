import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px

# ページ設定
st.set_page_config(page_title="配置馬券術 Web", layout="wide")

# ==========================================
# 1. 共通ロジック (既存の関数を移植)
# ==========================================

def to_half_width(text):
    if pd.isna(text): return text
    text = str(text)
    table = str.maketrans('０１２３４５６７８９', '0123456789')
    text = text.translate(table)
    text = re.sub(r'[^\d\.]', '', text)
    return text

def normalize_name(x):
    if pd.isna(x): return ''
    normalized_name = str(x).strip().replace('　', '').replace(' ', '')
    normalized_name = re.sub(r'[★☆▲△◇]', '', normalized_name)
    if ',' in normalized_name: normalized_name = normalized_name.split(',')[0]
    text = re.sub(r'[0-9\.]+[Rr]', '', normalized_name)
    text = re.sub(r'\(.*?\)', '', text)
    return text.replace('/', '').strip()

@st.cache_data
def load_data(file):
    try:
        if file.name.endswith('.csv'):
            df = pd.read_csv(file, encoding='cp932', on_bad_lines='skip')
        else:
            df = pd.read_excel(file, engine='openpyxl')
    except:
        return pd.DataFrame(), "エラー"

    df.columns = df.columns.str.strip()
    
    rename_map = {
        '場所': '場名', '開催': '場名', '単オッズ': '単ｵｯｽﾞ', 
        '調教師': '厩舎', '調教師名': '厩舎', '騎手名': '騎手',
        'レース': 'R', 'Ｒ': 'R', 'レース名': 'R',
        '着': '着順', '着 順': '着順', '番': '正番', '馬番': '正番'
    }
    df = df.rename(columns=rename_map)
    if '場名' not in df.columns: df['場名'] = 'Unknown'

    target_numeric_cols = ['R', '正番', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in target_numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(to_half_width)
            df[col] = pd.to_numeric(df[col], errors='coerce')

    df = df.dropna(subset=['R', '正番'])
    df['R'] = df['R'].astype(int)
    df['正番'] = df['正番'].astype(int)

    for col in ['騎手', '厩舎', '馬主']:
        if col in df.columns:
            df[col] = df[col].apply(normalize_name)
        else:
            df[col] = ''
            
    # 分析用データの必須列確保
    potential_cols = ['R', '場名', '馬名', '正番', '騎手', '厩舎', '馬主', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in potential_cols:
        if col not in df.columns: df[col] = np.nan

    return df[potential_cols].copy(), "success"

# --- 配置計算ロジック (省略せずそのまま使用) ---
def calc_haichi_numbers(df):
    if df[['逆番', '正循環', '逆循環']].notna().all().all():
        df['計算_逆番'] = df['逆番']
        return df
    max_umaban = df.groupby(['場名', 'R'])['正番'].transform('max')
    df['使用頭数'] = max_umaban.fillna(16).astype(int)
    if '頭数' in df.columns:
        df['使用頭数'] = df['頭数'].fillna(df['使用頭数']).astype(int)
    
    df['使用頭数'] = np.maximum(df['使用頭数'], df['正番'])
    
    def calc(row):
        t = int(row['使用頭数'])
        s = int(row['正番'])
        g = (t + 1) - s
        sj = t + s
        gj = t + g
        return pd.Series([g, sj, gj])
    
    df[['計算_逆番', '計算_正循環', '計算_逆循環']] = df.apply(calc, axis=1)
    return df

def get_pair_pattern(row1, row2):
    def val(x):
        try: return int(float(x)) 
        except: return None
    r1 = [val(row1['正番']), val(row1['計算_逆番']), val(row1['計算_正循環']), val(row1['計算_逆循環'])]
    r2 = [val(row2['正番']), val(row2['計算_逆番']), val(row2['計算_正循環']), val(row2['計算_逆循環'])]
    label = list("ABCDEFGHIJKLMNOP")
    pairs = [label[i * 4 + j] for i in range(4) for j in range(4)
             if r1[i] is not None and r2[j] is not None and r1[i] == r2[j] and r1[i] != 0]
    return ",".join(pairs)

def analyze_logic(df, df_prev=None):
    # ここにこれまでの find_all_pairs, get_blue_recommendations などのロジックを集約
    # (コードが長くなるため要約しますが、既存のロジックをそのままコピーして使えます)
    
    # 簡易版の実装（実際は既存の長い関数群をここに貼ります）
    df = calc_haichi_numbers(df)
    
    recommendations = []
    
    # 例: 騎手ペアの簡易探索
    df = df.sort_values(['場名', 'R'])
    for name, group in df.groupby('騎手'):
        if len(group) < 2: continue
        group = group.sort_values('R').to_dict('records')
        for i in range(len(group)-1):
            curr, next_r = group[i], group[i+1]
            pat = get_pair_pattern(curr, next_r)
            if pat:
                recommendations.append({
                    '場名': curr['場名'], 'R': curr['R'], '馬名': curr['馬名'], '正番': curr['正番'],
                    '騎手': name, 'タイプ': '騎手ペア', 'パターン': pat, '相手R': next_r['R'], 'スコア': 3.3
                })
                recommendations.append({
                    '場名': next_r['場名'], 'R': next_r['R'], '馬名': next_r['馬名'], '正番': next_r['正番'],
                    '騎手': name, 'タイプ': '騎手ペア', 'パターン': pat, '相手R': curr['R'], 'スコア': 3.3
                })

    if not recommendations:
        return pd.DataFrame()
        
    res_df = pd.DataFrame(recommendations)
    # 重複削除やマージ
    res_df = res_df.drop_duplicates(subset=['場名', 'R', '馬名'])
    
    # 必須列の整備
    if '着順' not in res_df.columns: res_df['着順'] = np.nan
    
    return res_df

# ==========================================
# 2. Webアプリ画面 (Streamlit)
# ==========================================

st.title("🏇 配置馬券術 リアルタイム分析")

# サイドバー: ファイルアップロード
with st.sidebar:
    st.header("データ入力")
    uploaded_file = st.file_uploader("当日データをアップロード", type=['xlsx', 'csv'])
    prev_file = st.file_uploader("前日データをアップロード (任意)", type=['xlsx', 'csv'])

if uploaded_file:
    # データ読み込み
    df_raw, status = load_data(uploaded_file)
    
    if status == "success":
        # 初回分析 (キャッシュを使って高速化も可能)
        if 'analyzed_df' not in st.session_state:
            with st.spinner('分析中...'):
                # ★ここで本来は全ロジックを実行
                # ここではデモ用に簡易ロジックを呼ぶ
                result_df = analyze_logic(df_raw)
                
                # 表示用に整理
                if not result_df.empty:
                    disp_cols = ['場名', 'R', '正番', '馬名', '騎手', 'タイプ', 'パターン', 'スコア', '着順']
                    # 列が存在するか確認してフィルタ
                    cols = [c for c in disp_cols if c in result_df.columns]
                    st.session_state['analyzed_df'] = result_df[cols].copy()
                else:
                    st.session_state['analyzed_df'] = pd.DataFrame()

        # ==========================================
        # 3. 編集可能なテーブル (これが重要！)
        # ==========================================
        st.subheader("📝 結果入力・分析")
        st.info("下の表の「着順」列をダブルクリックして入力すると、グラフとスコアが即座に更新されます。")

        if not st.session_state['analyzed_df'].empty:
            # ユーザーが編集できるデータフレーム
            edited_df = st.data_editor(
                st.session_state['analyzed_df'],
                column_config={
                    "着順": st.column_config.NumberColumn(
                        "着順",
                        help="1〜18の数値を入力",
                        min_value=1,
                        max_value=18,
                        step=1,
                        format="%d"
                    )
                },
                disabled=["場名", "R", "馬名", "騎手", "タイプ", "パターン"], # 着順以外は編集不可
                hide_index=True,
                use_container_width=True,
                height=500
            )

            # ==========================================
            # 4. リアルタイム集計 & グラフ
            # ==========================================
            
            # 着順が入力されたデータだけ抽出
            df_hits = edited_df[edited_df['着順'].notna()].copy()
            df_hits['着順'] = pd.to_numeric(df_hits['着順'], errors='coerce')
            
            # 的中（3着内）データ
            df_fuku = df_hits[df_hits['着順'] <= 3]

            st.divider()
            
            # --- リアルタイム指標 ---
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("消化レース数", len(df_hits['R'].unique()))
            with col2:
                # 複勝率
                fuku_rate = len(df_fuku) / len(df_hits) * 100 if len(df_hits) > 0 else 0
                st.metric("推奨馬 複勝率", f"{fuku_rate:.1f}%")
            with col3:
                # 的中パターン数
                st.metric("的中数", len(df_fuku))

            # --- 開催場・属性別の円グラフ ---
            st.subheader("📊 リアルタイム傾向グラフ")
            
            if not df_fuku.empty:
                # 開催場ごとにタブ分け
                places = df_fuku['場名'].unique()
                tabs = st.tabs(list(places))
                
                for tab, place in zip(tabs, places):
                    with tab:
                        place_data = df_fuku[df_fuku['場名'] == place]
                        
                        # パターン別集計
                        pat_counts = place_data['パターン'].value_counts().reset_index()
                        pat_counts.columns = ['パターン', '的中数']
                        
                        # 円グラフ (Plotly)
                        fig = px.pie(pat_counts, values='的中数', names='パターン', 
                                     title=f'{place} パターン別的中シェア',
                                     hole=0.4)
                        st.plotly_chart(fig, use_container_width=True)
                        
                        # データテーブル表示
                        st.dataframe(place_data[['R', '馬名', '騎手', 'パターン', '着順']], use_container_width=True)
            else:
                st.warning("まだ3着以内のデータがありません。着順を入力してください。")

            # --- 傾向を加味したスコア再計算 ---
            # (ここでは簡易的に、当たっているパターンの馬に +1.0 するロジック例)
            if not df_fuku.empty:
                hit_patterns = df_fuku['パターン'].unique()
                
                # まだ着順が入っていない馬（これから走る馬）
                future_races = edited_df[edited_df['着順'].isna()].copy()
                
                if not future_races.empty:
                    # 当たりパターンを持つ馬のスコアをアップ
                    future_races['傾向加点'] = future_races['パターン'].apply(lambda x: 2.0 if x in hit_patterns else 0.0)
                    future_races['予想スコア'] = future_races['スコア'] + future_races['傾向加点']
                    
                    st.subheader("📈 傾向を加味した推奨馬（これから走るレース）")
                    st.dataframe(
                        future_races.sort_values('予想スコア', ascending=False)[['場名', 'R', '馬名', 'パターン', 'スコア', '予想スコア']],
                        use_container_width=True
                    )

        else:
            st.warning("推奨馬が見つかりませんでした。")