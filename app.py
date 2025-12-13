import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px
import openpyxl

# ページ設定
st.set_page_config(page_title="配置馬券術 Web", layout="wide")

# ==========================================
# 1. 共通ロジック
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
    """
    ファイルを読み込み、エラーハンドリングを行う関数
    Windows(Shift-JIS)とMac(UTF-8)の両方に対応
    """
    df = None
    
    # 1. Excelファイルの処理
    if file.name.endswith('.xlsx'):
        try:
            file.seek(0)
            df = pd.read_excel(file, engine='openpyxl')
        except Exception as e:
            return pd.DataFrame(), f"Excel読み込みエラー: {e}"
            
    # 2. CSVファイルの処理（堅牢なロジック）
    else:
        # パターンA: UTF-8 で試行
        try:
            file.seek(0)
            df = pd.read_csv(file, encoding='utf-8', on_bad_lines='skip')
        except UnicodeDecodeError:
            # パターンB: 失敗したら CP932 (Shift-JIS) で再試行
            try:
                file.seek(0) # 必須: ファイルポインタを先頭に戻す
                df = pd.read_csv(file, encoding='cp932', on_bad_lines='skip')
            except Exception as e:
                return pd.DataFrame(), f"CSV読み込みエラー(文字コード判定不能): {e}"
        except Exception as e:
            return pd.DataFrame(), f"CSV予期せぬエラー: {e}"

    # データ整形ロジック
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

    # 必須列（R, 正番）がない場合はエラー
    if 'R' not in df.columns or '正番' not in df.columns:
        return pd.DataFrame(), "エラー: 必須列（レース名/R、馬番/正番）が見つかりません。"

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

# --- 配置計算ロジック ---
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

def analyze_logic(df):
    # ロジック実装部分
    df = calc_haichi_numbers(df)
    recommendations = []
    
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
    res_df = res_df.drop_duplicates(subset=['場名', 'R', '馬名'])
    
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
    # データ読み込み（強化版）
    df_raw, status = load_data(uploaded_file)
    
    if status != "success":
        st.error(status)
    else:
        # 初回分析 (キャッシュ制御)
        if 'analyzed_df' not in st.session_state:
            with st.spinner('分析中...'):
                result_df = analyze_logic(df_raw)
                
                if not result_df.empty:
                    disp_cols = ['場名', 'R', '正番', '馬名', '騎手', 'タイプ', 'パターン', 'スコア', '着順']
                    cols = [c for c in disp_cols if c in result_df.columns]
                    st.session_state['analyzed_df'] = result_df[cols].copy()
                else:
                    st.session_state['analyzed_df'] = pd.DataFrame()

        # ----------------------------------------------------
        # 3. 編集可能なテーブル
        # ----------------------------------------------------
        st.subheader("📝 結果入力・分析")
        st.info("下の表の「着順」列をダブルクリックして入力すると、グラフとスコアが即座に更新されます。")

        if not st.session_state['analyzed_df'].empty:
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
                disabled=["場名", "R", "馬名", "騎手", "タイプ", "パターン"], 
                hide_index=True,
                use_container_width=True,
                height=500,
                key='editor'
            )

            # ----------------------------------------------------
            # 4. リアルタイム集計 & グラフ (開催場別対応版)
            # ----------------------------------------------------
            
            # 着順が入力されたデータ（全レース）
            df_hits = edited_df[edited_df['着順'].notna()].copy()
            df_hits['着順'] = pd.to_numeric(df_hits['着順'], errors='coerce')
            
            # 的中（3着内）データ
            df_fuku = df_hits[df_hits['着順'] <= 3]

            st.divider()
            
            # --- 1. 全体の概要（合算） ---
            st.markdown("### 📊 全体ハイライト")
            col1, col2, col3 = st.columns(3)
            with col1:
                total_races = len(df_hits[['場名', 'R']].drop_duplicates())
                st.metric("総消化レース数", total_races)
            with col2:
                fuku_rate = len(df_fuku) / len(df_hits) * 100 if len(df_hits) > 0 else 0
                st.metric("全体複勝率", f"{fuku_rate:.1f}%")
            with col3:
                st.metric("総的中数", len(df_fuku))

            # --- 2. 開催場ごとの詳細集計 ---
            st.markdown("### 🏟️ 開催場別レポート")
            
            if not df_hits.empty:
                # 着順入力がある開催場を取得
                places = sorted(df_hits['場名'].unique())
                
                # 開催場ごとにタブを作成
                tabs = st.tabs(list(places))
                
                for tab, place in zip(tabs, places):
                    with tab:
                        # --- その場所だけのデータを抽出 ---
                        local_hits = df_hits[df_hits['場名'] == place]       
                        local_fuku = df_fuku[df_fuku['場名'] == place]       
                        local_races_count = len(local_hits['R'].unique())    
                        
                        # --- その場所の指標を表示 ---
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            c1.metric(f"{place} 消化R", local_races_count)
                        with c2:
                            local_rate = len(local_fuku) / len(local_hits) * 100 if len(local_hits) > 0 else 0
                            c2.metric("複勝率", f"{local_rate:.1f}%")
                        with c3:
                            c3.metric("的中数", len(local_fuku))
                        
                        st.divider()

                        # --- その場所の円グラフとリスト ---
                        if not local_fuku.empty:
                            col_graph, col_list = st.columns([1, 1])
                            
                            with col_graph:
                                pat_counts = local_fuku['パターン'].value_counts().reset_index()
                                pat_counts.columns = ['パターン', '的中数']
                                
                                fig = px.pie(pat_counts, values='的中数', names='パターン', 
                                             title=f'{place} パターン別傾向',
                                             hole=0.4)
                                st.plotly_chart(fig, use_container_width=True)
                            
                            with col_list:
                                st.caption(f"🎯 {place} の的中リスト")
                                st.dataframe(
                                    local_fuku[['R', '馬名', '騎手', 'パターン', '着順']], 
                                    use_container_width=True,
                                    height=300
                                )
                        else:
                            st.info(f"{place} ではまだ3着以内の的中がありません。")
            
            else:
                st.info("上の表で「着順」を入力すると、ここに開催場ごとの分析が表示されます。")

            # --- 3. 傾向を加味したスコア再計算 ---
            if not df_fuku.empty:
                hit_patterns = df_fuku['パターン'].unique()
                future_races = edited_df[edited_df['着順'].isna()].copy()
                
                if not future_races.empty:
                    future_races['傾向加点'] = future_races['パターン'].apply(lambda x: 2.0 if x in hit_patterns else 0.0)
                    future_races['予想スコア'] = future_races['スコア'] + future_races['傾向加点']
                    
                    st.subheader("📈 傾向を加味した推奨馬（これから走るレース）")
                    st.dataframe(
                        future_races.sort_values('予想スコア', ascending=False)[['場名', 'R', '馬名', 'パターン', 'スコア', '予想スコア']],
                        use_container_width=True
                    )

        else:
            st.warning("推奨馬が見つかりませんでした。")

