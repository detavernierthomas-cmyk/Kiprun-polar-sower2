import io
import base64
import json
import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field
from openai import OpenAI


class SeanceKiprun(BaseModel):
    date_jour: str = Field(description="Jour et date lisible (ex: Mercredi 25 nov.)")
    titre: str = Field(description="Nom de la séance (ex: VMA 8x400m)")
    echauffement: str = Field(description="Durée et allure de l'échauffement (ex: 20 min EF)")
    corps_seance: str = Field(description="Détail exact des répétitions, distances/durées, allures et récupérations")
    retour_calme: str = Field(description="Durée du retour au calme (ex: 10 min EF)")


class ListeSeances(BaseModel):
    seances: list[SeanceKiprun]


def image_to_base64(uploaded_file) -> str:
    img = Image.open(uploaded_file).convert("RGB")
    img.thumbnail((1200, 1200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


st.set_page_config(page_title="Lecteur Kiprun Pacer", page_icon="🏃")
st.title("🏃 Lecteur de plan Kiprun Pacer")

default_key = ""
try:
    default_key = st.secrets.get("OPENAI_API_KEY", "")
except Exception:
    pass

api_key = st.text_input("Clé API OpenAI", type="password", value=default_key)
fichiers = st.file_uploader(
    "📸 Dépose tes captures d'écran Kiprun",
    type=["png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True
)

if st.button("Analyser mes captures", type="primary"):
    if not fichiers or not api_key:
        st.warning("Ajoute tes captures et ta clé API.")
    else:
        try:
            with st.spinner("Lecture des séances..."):
                client = OpenAI(api_key=api_key.strip(), timeout=20.0)
                prompt = (
                    "Extrais toutes les séances visibles sur cette capture Kiprun Pacer. "
                    "Détaille clairement l'échauffement, le corps de séance (répétitions, temps/distance, allure, récup) "
                    "et le retour au calme."
                )
                for f in fichiers:
                    b64 = image_to_base64(f)
                    resp = client.beta.chat.completions.parse(
                        model="gpt-4o-mini",
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": prompt},
                                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                                ],
                            }
                        ],
                        response_format=ListeSeances,
                        temperature=0.1,
                    )
                    for s in resp.choices[0].message.parsed.seances:
                        with st.container(border=True):
                            st.subheader(f"📅 {s.date_jour} — {s.titre}")
                            st.write(f"**1. Échauffement :** {s.echauffement}")
                            st.write(f"**2. Corps de séance :** {s.corps_seance}")
                            st.write(f"**3. Retour au calme :** {s.retour_calme}")
        except Exception as e:
            st.error(f"Erreur : {e}")
          
