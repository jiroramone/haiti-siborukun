# ==========================================
            # 4. リアルタイム集計 & グラフ (開催場別対応版)
            # ==========================================
            
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
                # 全場での消化レース数
                total_races = len(df_hits[['場名', 'R']].drop_duplicates())
                st.metric("総消化レース数", total_races)
            with col2:
                # 全体での複勝率
                # 分母は「着順入力済みの全推奨馬数」、分子は「そのうち3着内の数」
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
                        local_hits = df_hits[df_hits['場名'] == place]       # この場所で結果入力済みの全データ
                        local_fuku = df_fuku[df_fuku['場名'] == place]       # この場所での的中データ
                        local_races_count = len(local_hits['R'].unique())    # この場所の消化レース数
                        
                        # --- その場所の指標を表示 ---
                        c1, c2, c3 = st.columns(3)
                        with c1:
                            c1.metric(f"{place} 消化R", local_races_count)
                        with c2:
                            # 場所別の複勝率
                            local_rate = len(local_fuku) / len(local_hits) * 100 if len(local_hits) > 0 else 0
                            c2.metric("複勝率", f"{local_rate:.1f}%")
                        with c3:
                            c3.metric("的中数", len(local_fuku))
                        
                        st.divider()

                        # --- その場所の円グラフとリスト ---
                        if not local_fuku.empty:
                            col_graph, col_list = st.columns([1, 1])
                            
                            with col_graph:
                                # パターン別集計
                                pat_counts = local_fuku['パターン'].value_counts().reset_index()
                                pat_counts.columns = ['パターン', '的中数']
                                
                                # 円グラフ
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

            # --- 3. 傾向を加味したスコア再計算（ここは全体の傾向で良いか、場所別にするかは好みですが、一旦全体傾向で実装） ---
            # ... (以下のロジックは前回と同じでOK)
