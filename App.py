if st.button("1️⃣ Analyser les captures", type="primary"):
    if not fichiers or not gemini_key:
        st.warning("Ajoute tes captures et ta clé Gemini.")
    else:
        try:
            with st.spinner("Lecture des séances..."):
                client = genai.Client(api_key=gemini_key.strip())
                prompt = "Extrais les séances Kiprun Pacer. Convertis 1h15 -> 75', prends l'allure moyenne si fourchette, sépare Échauffement + Corps + RAC."
                seances = []
                for f in fichiers:
                    img = Image.open(f).convert("RGB")
                    resp = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=[prompt, img],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=ListeSeances,
                            temperature=0.1,
                        ),
                    )
                    for s in json.loads(resp.text).get("seances", []):
                        seances.append((s["date_iso"], s["titre"], s["syntaxe_parseur"]))
                uniques = sorted(list(set(seances)), key=lambda x: x[0])
                st.session_state.parseur = "\n".join([f"{d} | {t} | {syn}" for d, t, syn in uniques])
                st.rerun()
        except Exception as e:
            st.error(f"Détail exact de l'erreur API : {e}")
