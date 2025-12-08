import streamlit as st
import pandas as pd
import numpy as np
import re
import plotly.express as px

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
            
    potential_cols = ['R', '場名', '馬名', '正番', '騎手', '厩舎', '馬主', '単ｵｯｽﾞ', '逆番', '正循環', '逆循環', '頭数']
    for col in potential_cols:
        if col not in df.columns: df[col] = np.nan

    return df[potential_cols].copy(), "success"

def calc_haichi_numbers(df):
    if df[['逆番', '正循環', '逆循環']].notna().all().all():
        df['計算_逆番'] = df['逆番']
        df['計算_正循環'] = df['正循環']
        df['計算_逆循環'] = df['逆循環']
        return df
    
    # 頭数自動計算 (地方競馬対応)
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

# ==========================================
# 2. 分析ロジック (判定処理)
# ==========================================
def analyze_logic(df):
    df = calc_haichi_numbers(df)
    recommendations = []
    
    # データをソートしておく
    df = df.sort_values(['場名', 'R', '正番'])
    
    # --- 1. 騎手ペア ---
    for name, group in df.groupby('騎手'):
        if len(group) < 2: continue
        group = group.sort_values('R').to_dict('records')
        for i in range(len(group)-1):
            curr, next_r = group[i], group[i+1]
            if curr['場名'] != next_r['場名']: continue # 同場のみ
            pat = get_pair_pattern(curr, next_r)
            if pat:
                # 相互に登録
                recommendations.append({
                    '場名': curr['場名'], 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'],
                    '騎手': name, 'タイプ': '騎手ペア', 'パターン': pat, '相手R': next_r['R'], 'スコア': 3.3
                })
                recommendations.append({
                    '場名': next_r['場名'], 'R': next_r['R'], '正番': next_r['正番'], '馬名': next_r['馬名'],
                    '騎手': name, 'タイプ': '騎手ペア', 'パターン': pat, '相手R': curr['R'], 'スコア': 3.3
                })

    # --- 2. 厩舎ペア ---
    if '厩舎' in df.columns:
        for (place, name), group in df.groupby(['場名', '厩舎']):
            if len(group) < 2: continue
            group = group.sort_values('R').to_dict('records')
            for i in range(len(group)):
                for j in range(i+1, len(group)):
                    curr, next_r = group[i], group[j]
                    pat = get_pair_pattern(curr, next_r)
                    if pat:
                        recommendations.append({
                            '場名': curr['場名'], 'R': curr['R'], '正番': curr['正番'], '馬名': curr['馬名'],
                            '騎手': f"(厩舎:{name})", 'タイプ': '厩舎ペア', 'パターン': pat, '相手R': next_r['R'], 'スコア': 3.2
                        })
                        recommendations.append({
                            '場名': next_r['場名'], 'R': next_r['R'], '正番': next_r['正番'], '馬名': next_r['馬名'],
                            '騎手': f"(厩舎:{name})", 'タイプ': '厩舎ペア', 'パターン': pat, '相手R': curr['R'], 'スコア': 3.2
                        })

    if not recommendations:
        return pd.DataFrame()
        
    res_df = pd.DataFrame(recommendations)
    
    # 重複削除 (同じ馬が複数の理由で選ばれた場合、スコアを加算して統合)
    # まず馬ごとにグループ化
    agg_funcs = {
        '騎手': 'first',
        'タイプ': lambda x: '/'.join(sorted(set(x))),
        'パターン': lambda x: ','.join(sorted(set(x))),
        'スコア': 'sum',
        '正番': 'first' # ソート用
    }
    
    # 必要な列だけでグルーピング
    res_df = res_df.groupby(['場名', 'R', '馬名'], as_index=False).agg(agg_funcs)
    
    # ★重要: レース順に並べ替え (ここを追加！)
    res_df = res_df.sort_values(['場名', 'R', '正番'], ascending=[True, True, True])
    
    # 着順列の初期化
    res_df['着順'] = np.nan
    
    return res_df

# ==========================================
# 3. Webアプリ画面 (Streamlit)
# ==========================================

st.title("🏇 配置馬券術 リアルタイム分析")
st.caption("着順を入力すると、統計とスコアが即座に更新されます。")

# サイドバー
with st.sidebar:
    st.header("データ入力")
    uploaded_file = st.file_uploader("当日データをアップロード", type=['xlsx', 'csv'])
    st.markdown("---")
    st.write("※Excelファイル(.xlsx)またはCSVファイル(.csv)に対応しています。")

if uploaded_file:
    df_raw, status = load_data(uploaded_file)
    
    if status == "success":
        # 初回分析 (session_stateで保持)
        if 'analyzed_df' not in st.session_state:
            with st.spinner('分析中...'):
                result_df = analyze_logic(df_raw)
                if not result_df.empty:
                    # 編集用IDを作成 (Streamlitの仕様対策)
                    result_df['id'] = result_df.index
                    st.session_state['analyzed_df'] = result_df
                else:
                    st.session_state['analyzed_df'] = pd.DataFrame()

        # --- メインエリア ---
        if not st.session_state['analyzed_df'].empty:
            
            # --- 1. データエディタ (着順入力) ---
            st.subheader("📝 結果入力・推奨馬リスト")
            
            # 表示する列を整理
            display_df = st.session_state['analyzed_df'].copy()
            
            edited_df = st.data_editor(
                display_df,
                column_config={
                    "着順": st.column_config.NumberColumn(
                        "着順 (入力)",
                        help="確定した着順を入力してください (1〜18)",
                        min_value=1,
                        max_value=18,
                        step=1,
                        format="%d"
                    ),
                    "スコア": st.column_config.ProgressColumn(
                        "重要度",
                        format="%.1f",
                        min_value=0,
                        max_value=15,
                    ),
                },
                disabled=["場名", "R", "馬名", "正番", "騎手", "タイプ", "パターン", "スコア"],
                hide_index=True,
                use_container_width=True,
                height=500,
                key="editor" # キーを指定して状態を維持
            )
            
            # 入力されたデータをセッションステートに反映
            # (data_editorは自動でstateを更新しない場合があるため念のため)
            if edited_df is not None:
                st.session_state['analyzed_df'] = edited_df

            # ==========================================
            # 4. リアルタイム集計 & グラフ
            # ==========================================
            
            df_hits = edited_df[edited_df['着順'].notna()].copy()
            df_hits['着順'] = pd.to_numeric(df_hits['着順'], errors='coerce')
            df_fuku = df_hits[df_hits['着順'] <= 3]

            st.markdown("---")
            st.subheader("📊 リアルタイム分析レポート")

            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("消化レース数", len(df_hits['R'].unique()))
            with col2:
                # 複勝率
                fuku_rate = len(df_fuku) / len(df_hits) * 100 if len(df_hits) > 0 else 0
                st.metric("推奨馬 複勝率", f"{fuku_rate:.1f}%")
            with col3:
                # 単勝回収率 (オッズデータがないので仮)
                # 実装するならload_dataで単オッズを読み込み計算する
                st.metric("的中数", f"{len(df_fuku)} 頭")

            if not df_fuku.empty:
                # 開催場ごとにタブ分け
                places = sorted(df_fuku['場名'].unique())
                tabs = st.tabs(places)
                
                for tab, place in zip(tabs, places):
                    with tab:
                        col_g1, col_g2 = st.columns(2)
                        
                        place_data = df_fuku[df_fuku['場名'] == place]
                        
                        # パターン別集計 (カンマ区切りを展開)
                        all_patterns = []
                        for p in place_data['パターン']:
                            if p: all_patterns.extend(p.split(','))
                        
                        if all_patterns:
                            pat_counts = pd.Series(all_patterns).value_counts().reset_index()
                            pat_counts.columns = ['パターン', '的中数']
                            
                            with col_g1:
                                fig = px.pie(pat_counts, values='的中数', names='パターン', 
                                            title=f'【{place}】 パターン別 的中シェア',
                                            hole=0.4)
                                st.plotly_chart(fig, use_container_width=True)
                            
                            with col_g2:
                                st.write(f"**{place} の的中馬一覧**")
                                st.dataframe(place_data[['R', '馬名', '騎手', 'パターン', '着順']], use_container_width=True, hide_index=True)
                        else:
                            st.info("パターンデータがありません")

                # --- 傾向スコア加算 ---
                st.markdown("### 📈 次のレースの注目馬 (傾向加味)")
                hit_patterns = set()
                for p in df_fuku['パターン']:
                    if p: hit_patterns.update(p.split(','))
                
                # まだ着順が入っていない馬
                future_races = edited_df[edited_df['着順'].isna()].copy()
                
                if not future_races.empty:
                    # 当たりパターンを持っているか判定
                    def calc_trend_bonus(row_pat):
                        if not row_pat: return 0
                        pats = row_pat.split(',')
                        bonus = 0
                        for p in pats:
                            if p in hit_patterns: bonus += 1.0 # ヒットしたパターン1つにつき+1点
                        return bonus

                    future_races['傾向加点'] = future_races['パターン'].apply(calc_trend_bonus)
                    future_races['総合スコア'] = future_races['スコア'] + future_races['傾向加点']
                    
                    # 傾向加点がある馬のみ、またはスコア上位を表示
                    hot_horses = future_races[future_races['傾向加点'] > 0].sort_values(['場名', 'R', '総合スコア'], ascending=[True, True, False])
                    
                    if not hot_horses.empty:
                        st.success("本日の当たりパターンを持つ馬が検出されました！")
                        st.dataframe(
                            hot_horses[['場名', 'R', '馬名', '騎手', 'パターン', 'スコア', '傾向加点', '総合スコア']],
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.info("現在、傾向と合致する未出走馬はありません。")
            else:
                st.info("的中データがまだありません。レース結果を入力してください。")

        else:
            st.warning("推奨対象となる馬が見つかりませんでした。")
    else:
        st.error("ファイルの読み込みに失敗しました。形式を確認してください。")
