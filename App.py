import os
import re
import json
import requests
import streamlit as st
from PIL import Image
from pydantic import BaseModel, Field
from google import genai
from google.genai import types

class PolarFlowClient:
    BASE_URL = "https://flow.polar.com"

    def __init__(self, email: str, password: str):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/login"
        })
        self.email = email
        self.password = password

    def login(self):
        self.session.get(f"{self.BASE_URL}/login")
        res = self.session.post(
            f"{self.BASE_URL}/login",
            data={"email": self.email, "password": self.password, "returnUrl": "/diary"}
        )
        if "diary" not in res.url and res.status_code != 200:
            raise ConnectionError("Échec de connexion à Polar Flow.")

    @staticmethod
    def _parse_token(token: str) -> dict:
        token = token.strip()
        m_dist = re.search(r"(\d+)\s*m\b", token, re.IGNORECASE)
        if m_dist:
            return {"type": "DISTANCE", "distance_m": int(m_dist.group(1)), "duration_sec": 0}
        m_time = re.search(r"(\d+)'(\d+)?", token)
        if m_time:
            minutes = int(m_time.group(1))
            seconds = int(m_time.group(2)) if m_time.group(2) else 0
            return {"type": "DURATION", "duration_sec": minutes * 60 + seconds, "distance_m": 0}
        raise ValueError(f"Format non reconnu : '{token}'")

    def parse_workout_string(self, workout_str: str) -> list:
        phases = []
        blocks = [b.strip() for b in workout_str.split("+")]
        for block in blocks:
            m_rep = re.match(r"(\d+)\s*[xX*]\s*\((.+?)/(.+?)\)", block)
            if m_rep:
                reps = int(m_rep.group(1))
                effort_raw, recup_raw = m_rep.group(2).strip(), m_rep.group(3).strip()
                effort_spec = self._parse_token(effort_raw)
                recup_spec = self._parse_token(recup_raw)
                for i in range(1, reps + 1):
                    phases.append({"name": f"Effort {i}/{reps} ({effort_raw})", **effort_spec})
                    phases.append({"name": f"Récup {i}/{reps} ({recup_raw})", **recup_spec})
            else:
                spec = self._parse_token(block)
                phases.append({"name": block[:35], **spec})
        return phases

    def create_phased_workout(self, date_str: str, title: str, workout_str: str) -> str:
        raw_phases = self.parse_workout_string(workout_str)
        polar_phases = [
            {
                "order": idx,
                "name": p["name"],
                "changeType": "AUTOMATIC",
                "goalType": p["type"],
                "duration": p["duration_sec"],
                "distance": p["distance_m"],
                "intensityType": "NONE"
            }
            for idx, p in enumerate(raw_phases, start=1)
        ]
        payload = {
            "sportId": 1,
            "name": title[:45],
            "description": f"Plan Kiprun Pacer : {workout_str}",
            "startDate": f"{date_str} 18:00:00",
            "type": "PHASED",
            "phases": polar_phases
        }
        res = self.session.post(
            f"{self.BASE_URL}/training/target/create",
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"}
        )
        if res.status_code in (200, 201):
            return f"✅ {date_str} | {title} ({len(polar_phases)} phases)"
        return f"❌ {date_str} | Erreur {res.status_code}"

class SeanceKiprun(BaseModel):
    date_iso: str = Field(description="Date YYYY-MM-DD (année 2026).")
    titre: str = Field(description="Titre court 'Kiprun - ...', max 40 caractères.")
    syntaxe_parseur: str = Field(
        description="Format strict : '45\\' EF' ou '20\\' EF + 8 x (400m @ 3:55 / 1\\'00) + 10\\' RAC'"
    )

class ListeSeances(BaseModel):
    seances: list[SeanceKiprun]

st.set_page_config(page_title="Kiprun → Polar", page_icon="🏃")
st.title("🏃 Kiprun Pacer → Polar Vantage M2")

default_key = ""
try:
    default_key = st.secrets.get("GEMINI_API_KEY", "")
except Exception:
    pass

gemini_key = st.text_input("Clé API Gemini", type="password", value=default_key)
fichiers = st.file_uploader("📸 Dépose tes captures d'écran Kiprun", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=True)

if "parseur" not in st.session_state:
    st.session_state.parseur = ""

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

texte_edit = st.text_area("📝 Parseur généré (modifiable)", value=st.session_state.parseur, height=180)

st.subheader("2️⃣ Envoi vers Polar Flow")
col1, col2 = st.columns(2)
email = col1.text_input("Email Polar Flow")
password = col2.text_input("Mot de passe Polar", type="password")

if st.button("🚀 Planifier sur ma Polar Vantage M2"):
    try:
        polar = PolarFlowClient(email, password)
        polar.login()
        for ligne in texte_edit.strip().splitlines():
            parts = [p.strip() for p in ligne.split("|")]
            if len(parts) == 3:
                st.write(polar.create_phased_workout(parts[0], parts[1], parts[2]))
    except Exception as e:
        st.error(f"Erreur : {e}")
  
