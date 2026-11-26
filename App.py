import os
import re
import io
import base64
import json
import requests
import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field
from openai import OpenAI


class IntervalsClient:
    BASE_URL = "https://intervals.icu/api/v1"

    def __init__(self, athlete_id: str, api_key: str):
        self.athlete_id = athlete_id.strip()
        self.auth = ("API_KEY", api_key.strip())

    @staticmethod
    def to_intervals_description(workout_str: str) -> str:
        """
        Convertit '20' EF + 8 x (400m @ 3:55 / 1'00) + 10' RAC'
        au format texte structuré natif d'Intervals.icu (synchronisé vers Polar).
        """
        lignes_icu = []
        blocks = [b.strip() for b in workout_str.split("+")]

        def format_step(step_raw: str, default_intensity: str = "Z2 Pace") -> str:
            step_raw = step_raw.strip()
            # Extraction d'une allure éventuelle (ex: @ 3:55)
            pace_match = re.search(r"@\s*(\d+:\d+)", step_raw)
            cible = f"{pace_match.group(1)}/km Pace" if pace_match else default_intensity

            # Distance (ex: 400m, 1000m)
            m_dist = re.search(r"(\d+)\s*m\b", step_raw, re.IGNORECASE)
            if m_dist:
                dist_m = int(m_dist.group(1))
                dist_str = f"{dist_m / 1000:g}km" if dist_m >= 1000 else f"{dist_m}m"
                return f"- {dist_str} {cible}"

            # Durée (ex: 20', 1'30)
            m_time = re.search(r"(\d+)'(\d+)?", step_raw)
            if m_time:
                mins = int(m_time.group(1))
                secs = int(m_time.group(2)) if m_time.group(2) else 0
                time_str = f"{mins}m{secs}s" if secs else f"{mins}m"
                return f"- {time_str} {cible}"

            return f"- 10m {cible}"

        for idx, block in enumerate(blocks):
            m_rep = re.match(r"(\d+)\s*[xX*]\s*\((.+?)/(.+?)\)", block)
            if m_rep:
                reps = int(m_rep.group(1))
                effort_raw = m_rep.group(2).strip()
                recup_raw = m_rep.group(3).strip()
                lignes_icu.append(f"\nMain Set {reps}x")
                lignes_icu.append(format_step(effort_raw, "Z4 Pace"))
                lignes_icu.append(format_step(recup_raw, "Z1 Pace"))
                lignes_icu.append("")
            else:
                if idx == 0 and len(blocks) > 1:
                    lignes_icu.append("Warmup")
                    lignes_icu.append(format_step(block, "Z1-Z2 Pace"))
                elif idx == len(blocks) - 1 and len(blocks) > 1:
                    lignes_icu.append("Cooldown")
                    lignes_icu.append(format_step(block, "Z1 Pace"))
                else:
                    lignes_icu.append(format_step(block, "Z2 Pace"))

        return "\n".join(lignes_icu).strip()

    def create_workout(self, date_str: str, title: str, workout_str: str) -> str:
        description_icu = self.to_intervals_description(workout_str)
        payload = {
            "category": "WORKOUT",
            "type": "Run",
            "start_date_local": f"{date_str}T18:00:00",
            "name": title[:45],
            "description": description_icu,
        }
        url = f"{self.BASE_URL}/athlete/{self.athlete_id}/events"
        res = requests.post(url, auth=self.auth, json=payload, timeout=15)

        if res.status_code in (200, 201):
            return f"✅ {date_str} | {title} -> Envoyé sur Intervals.icu (synchro Polar)"
        return f"❌ {date_str} | Erreur {res.status_code} : {res.text[:150]}"


class SeanceKiprun(BaseModel):
    date_iso: str = Field(description="Date YYYY-MM-DD (année 2026).")
    titre: str = Field(description="Titre court 'Kiprun - ...', max 40 caractères.")
    syntaxe_parseur: str = Field(
        description="Format strict : '45\\' EF' ou '20\\' EF + 8 x (400m @ 3:55 / 1\\'00) + 10\\' RAC'"
    )


class ListeSeances(BaseModel):
    seances: list[SeanceKiprun]


def image_to_base64(uploaded_file) -> str:
    img = Image.open(uploaded_file).convert("RGB")
    img.thumbnail((1200, 1200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


st.set_page_config(page_title="Kiprun → Intervals → Polar", page_icon="🏃")
st.title("🏃 Kiprun Pacer → Polar Vantage M2")

# Lecture des secrets Streamlit s'ils existent
def get_secret(name: str) -> str:
    try:
        return st.secrets.get(name, "")
    except Exception:
        return ""

api_key = st.text_input("Clé API OpenAI (sk-...)", type="password", value=get_secret("OPENAI_API_KEY"))
fichiers = st.file_uploader(
    "📸 Dépose tes captures d'écran Kiprun",
    type=["png", "jpg", "jpeg", "webp"],
    accept_multiple_files=True
)

if "parseur" not in st.session_state:
    st.session_state.parseur = ""

if st.button("1️⃣ Analyser les captures", type="primary"):
    if not fichiers or not api_key:
        st.warning("Ajoute tes captures et ta clé API OpenAI.")
    else:
        try:
            with st.spinner("Analyse des captures en cours..."):
                client = OpenAI(api_key=api_key.strip(), timeout=20.0)
                prompt = (
                    "Extrais toutes les séances visibles sur cette capture Kiprun Pacer. "
                    "Convertis les heures en minutes (ex: 1h15 -> 75'). "
                    "Si une allure est une fourchette (ex: 4:10-4:20/km), prends la moyenne (4:15). "
                    "Sépare toujours : [Échauffement] + [Corps de séance] + [Retour au calme]. "
                    "Si footing simple sans bloc d'allure : juste la durée totale (ex: 50' EF)."
                )
                seances = []
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
                    parsed = resp.choices[0].message.parsed
                    for s in parsed.seances:
                        seances.append((s.date_iso, s.titre, s.syntaxe_parseur))

                uniques = sorted(list(set(seances)), key=lambda x: x[0])
                st.session_state.parseur = "\n".join([f"{d} | {t} | {syn}" for d, t, syn in uniques])
                st.rerun()
        except Exception as e:
            st.error(f"Erreur API : {e}")

texte_edit = st.text_area("📝 Parseur généré (modifiable)", value=st.session_state.parseur, height=180)

st.subheader("2️⃣ Envoi vers Intervals.icu → Polar Flow")
col1, col2 = st.columns(2)
athlete_id = col1.text_input("Athlete ID (ex: i123456)", value=get_secret("INTERVALS_ATHLETE_ID"))
intervals_key = col2.text_input("Clé API Intervals.icu", type="password", value=get_secret("INTERVALS_API_KEY"))

if st.button("🚀 Planifier sur ma Polar Vantage M2"):
    if not athlete_id or not intervals_key:
        st.warning("Renseigne ton Athlete ID et ta clé API Intervals.icu.")
    else:
        try:
            icu = IntervalsClient(athlete_id, intervals_key)
            for ligne in texte_edit.strip().splitlines():
                parts = [p.strip() for p in ligne.split("|")]
                if len(parts) == 3:
                    st.write(icu.create_workout(parts[0], parts[1], parts[2]))
        except Exception as e:
            st.error(f"Erreur : {e}")
  
