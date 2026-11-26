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

class PolarFlowClient:
    BASE_URL = "https://flow.polar.com"

    def __init__(self, email: str, password: str):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Origin": self.BASE_URL,
            "Referer": f"{self.BASE_URL}/diary"
        })
        self.email = email
        self.password = password
        self.csrf_token = ""

    def login(self):
        # 1. Ouverture de la page de connexion
        r_init = self.session.get(f"{self.BASE_URL}/login")
        m_csrf = re.search(r'name="csrfToken"\s+value="([^"]+)"', r_init.text)
        login_data = {"email": self.email, "password": self.password, "returnUrl": "/diary"}
        if m_csrf:
            login_data["csrfToken"] = m_csrf.group(1)

        # 2. Envoi des identifiants
        res = self.session.post(f"{self.BASE_URL}/login", data=login_data)
        if "login" in res.url and "diary" not in res.url:
            raise ConnectionError("Échec de connexion à Polar Flow. Vérifie ton email/mot de passe.")

        # 3. Récupération du jeton CSRF actif sur la page Agenda (/diary)
        diary_page = self.session.get(f"{self.BASE_URL}/diary")
        token_match = (
            re.search(r'"csrfToken"\s*:\s*"([^"]+)"', diary_page.text)
            or re.search(r'content="([^"]+)"\s+name="csrf-token"', diary_page.text)
            or re.search(r'name="_csrf"\s+content="([^"]+)"', diary_page.text)
        )
        if token_match:
            self.csrf_token = token_match.group(1)
        elif "CSRF-TOKEN" in self.session.cookies:
            self.csrf_token = self.session.cookies["CSRF-TOKEN"]

        if self.csrf_token:
            self.session.headers.update({"X-CSRF-Token": self.csrf_token})

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

        endpoints = [
            "/api/training-targets",
            "/training/targets",
            "/training/target/create",
        ]

        headers = {"Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"}
        dernier_code = 404
        dernier_texte = ""

        for ep in endpoints:
            res = self.session.post(
                f"{self.BASE_URL}{ep}",
                data=json.dumps(payload),
                headers=headers
            )
            dernier_code = res.status_code
            dernier_texte = res.text[:150]
            if res.status_code in (200, 201):
                return f"✅ {date_str} | {title} ({len(polar_phases)} phases)"

        return f"❌ {date_str} | Erreur {dernier_code} ({dernier_texte})"


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


st.set_page_config(page_title="Kiprun → Polar", page_icon="🏃")
st.title("🏃 Kiprun Pacer → Polar Vantage M2")

default_key = ""
try:
    default_key = st.secrets.get("OPENAI_API_KEY", "")
except Exception:
    pass

api_key = st.text_input("Clé API OpenAI (sk-...)", type="password", value=default_key)
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
            with st.spinner("Analyse ultra-rapide en cours..."):
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
      
