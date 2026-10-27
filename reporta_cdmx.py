# /// script
# requires-python = ">=3.10, <3.14"
# dependencies = [
#     "google-genai>=1.0.0",
#     "pillow>=10.0.0",
#     "pydantic>=2.0.0",
#     "python-dotenv>=1.0.0",
#     "rich>=13.0.0",
# ]
# ///
"""
Reporta CDMX — Inspector Comunitario de Infraestructura Urbana con IA (PEP 723)
================================================================================
Incluye:
  1. Portal Ciudadano (/) con captura fotográfica y análisis multimodal de Gemini.
  2. Base de datos SQLite integrada (reporta_cdmx.db) con historial persistente.
  3. Panel de Administración y Despacho de Cuadrillas (/admin) con KPIs y estatus.
  4. Modo CLI para inspecciones desde terminal.

Ejecución:
  uv run reporta_cdmx.py                 # Inicia portal ciudadano y panel admin
  uv run reporta_cdmx.py bache.jpg       # Analiza imagen y guarda en BD vía CLI
"""

import argparse
import base64
import csv
import io
import json
import os
import random
import shutil
import sqlite3
import sys
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from PIL import Image
from pydantic import BaseModel, Field
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Cargar variables de entorno desde .env
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = Path(__file__).resolve().parent / ".env"
    if _env_path.exists():
        for _line in _env_path.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip("'\""))

console = Console()

# Rutas de base de datos y archivos
PROJECT_DIR = Path(__file__).resolve().parent
DB_PATH = PROJECT_DIR / "reporta_cdmx.db"
UPLOADS_DIR = PROJECT_DIR / "uploads"
EXAMPLES_DIR = PROJECT_DIR / "examples"

UPLOADS_DIR.mkdir(exist_ok=True)


# ==============================================================================
# 1. Base de Datos SQLite (Persistencia Local)
# ==============================================================================
def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Inicializa la base de datos y siembra datos de ejemplo si está vacía."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                folio TEXT UNIQUE NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                categoria_suac TEXT NOT NULL,
                subcategoria TEXT NOT NULL,
                dependencia_responsable TEXT NOT NULL,
                prioridad TEXT NOT NULL,
                evaluacion_riesgo TEXT,
                descripcion_tecnica TEXT,
                accion_inmediata TEXT,
                tiempo_estimado TEXT,
                mensaje_whatsapp TEXT,
                image_filename TEXT,
                estatus TEXT DEFAULT 'Pendiente'
            )
        """)
        conn.commit()

        # Sembrar datos iniciales si no hay registros
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM reports")
        count = cursor.fetchone()[0]

        if count == 0:
            console.print("[dim]Inicializando base de datos con casos de prueba CDMX...[/dim]")
            
            # Sembrar caso 1: Bache
            bache_img_src = EXAMPLES_DIR / "bache.webp"
            bache_img_name = "SUAC-2026-DEMO-BACHE.webp"
            if bache_img_src.exists():
                shutil.copy(bache_img_src, UPLOADS_DIR / bache_img_name)
            
            cursor.execute("""
                INSERT INTO reports (
                    folio, categoria_suac, subcategoria, dependencia_responsable,
                    prioridad, evaluacion_riesgo, descripcion_tecnica, accion_inmediata,
                    tiempo_estimado, mensaje_whatsapp, image_filename, estatus
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "SUAC-2026-094321",
                "Mantenimiento Asfáltico",
                "Bache profundo / Deterioro severo de carpeta asfáltica",
                "Alcaldía Local",
                "Alta",
                "Riesgo de daño severo a suspensión de vehículos y caída de ciclistas debido a las dimensiones del bache.",
                "Falla severa en la carpeta asfáltica consistente en un bache de aprox 70cm con desprendimiento pétreo y fracturas radiales junto a rejilla pluvial.",
                "Corte de caja, retiro de escombro y bacheo con mezcla asfáltica en caliente.",
                "72 horas",
                "Hola! Tu reporte con folio SUAC-2026-094321 ha sido turnado a la Alcaldía para su atención prioritaria.",
                bache_img_name if bache_img_src.exists() else None,
                "En Cuadrilla"
            ))

            # Sembrar caso 2: Fuga de Agua
            fuga_img_src = EXAMPLES_DIR / "fuga.jpg"
            fuga_img_name = "SUAC-2026-DEMO-FUGA.jpg"
            if fuga_img_src.exists():
                shutil.copy(fuga_img_src, UPLOADS_DIR / fuga_img_name)

            cursor.execute("""
                INSERT INTO reports (
                    folio, categoria_suac, subcategoria, dependencia_responsable,
                    prioridad, evaluacion_riesgo, descripcion_tecnica, accion_inmediata,
                    tiempo_estimado, mensaje_whatsapp, image_filename, estatus
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "SUAC-2026-904128",
                "Agua Potable y Drenaje",
                "Fuga de agua potable en vía pública",
                "SACMEX",
                "Alta",
                "Desperdicio continuo de recurso hídrico crítico y reblandecimiento del asfalto.",
                "Encharcamiento abundante y flujo continuo cubriendo carril de circulación, derivado de probable fisura en red de distribución.",
                "Despliegue de cuadrilla SACMEX para sondeo de fuga, cierre de válvula y reemplazo de tubería.",
                "24 horas",
                "Hola. Tu reporte de fuga de agua folio SUAC-2026-904128 fue asignado a SACMEX con máxima prioridad.",
                fuga_img_name if fuga_img_src.exists() else None,
                "Pendiente"
            ))
            conn.commit()


def save_report_to_db(report: "SUACReport", image_bytes: Optional[bytes] = None, ext: str = ".jpg") -> str:
    """Guarda un reporte y su imagen en la base de datos SQLite."""
    image_filename = None
    if image_bytes:
        image_filename = f"{report.folio_simulado}{ext}"
        image_file_path = UPLOADS_DIR / image_filename
        image_file_path.write_bytes(image_bytes)

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO reports (
                folio, categoria_suac, subcategoria, dependencia_responsable,
                prioridad, evaluacion_riesgo, descripcion_tecnica, accion_inmediata,
                tiempo_estimado, mensaje_whatsapp, image_filename, estatus
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            report.folio_simulado,
            report.categoria_suac,
            report.subcategoria,
            report.dependencia_responsable,
            report.prioridad,
            report.evaluacion_riesgo,
            report.descripcion_tecnica,
            report.accion_inmediata_cuadrilla,
            report.tiempo_estimado_atencion,
            report.notificacion_ciudadano_whatsapp,
            image_filename,
            "Pendiente"
        ))
        conn.commit()
    return report.folio_simulado


def get_all_reports(dependencia: Optional[str] = None, estatus: Optional[str] = None) -> List[Dict[str, Any]]:
    """Consulta reportes con filtros opcionales."""
    query = "SELECT * FROM reports WHERE 1=1"
    params = []

    if dependencia and dependencia != "Todos":
        query += " AND dependencia_responsable LIKE ?"
        params.append(f"%{dependencia}%")

    if estatus and estatus != "Todos":
        query += " AND estatus = ?"
        params.append(estatus)

    query += " ORDER BY id DESC"

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def update_report_status(folio: str, new_status: str) -> bool:
    """Actualiza el estado de un reporte (Pendiente, En Cuadrilla, Atendido, Cancelado)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reports SET estatus = ? WHERE folio = ?", (new_status, folio))
        conn.commit()
        return cursor.rowcount > 0


def get_kpis() -> Dict[str, Any]:
    """Calcula indicadores clave para la mesa de control."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM reports")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reports WHERE dependencia_responsable LIKE '%SACMEX%'")
        sacmex_total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reports WHERE categoria_suac LIKE '%Asfáltico%' OR categoria_suac LIKE '%Bache%'")
        baches_total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reports WHERE estatus = 'Atendido'")
        atendidos = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reports WHERE estatus = 'Pendiente'")
        pendientes = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM reports WHERE estatus = 'En Cuadrilla'")
        en_cuadrilla = cursor.fetchone()[0]

        return {
            "total": total,
            "sacmex": sacmex_total,
            "baches": baches_total,
            "atendidos": atendidos,
            "pendientes": pendientes,
            "en_cuadrilla": en_cuadrilla,
        }


# ==============================================================================
# 2. Esquema Pydantic y Catálogo Oficial SUAC
# ==============================================================================
class SUACReport(BaseModel):
    folio_simulado: str = Field(
        default="",
        description="Identificador único del reporte (ej. SUAC-2026-089412)"
    )
    categoria_suac: str = Field(
        description="Categoría oficial en el catálogo SUAC (ej. 'Agua Potable y Drenaje', 'Mantenimiento Asfáltico', 'Alumbrado Público', 'Limpia')"
    )
    subcategoria: str = Field(
        description="Subcategoría específica (ej. 'Fuga de agua potable en banqueta', 'Bache profundo', 'Luminaria apagada')"
    )
    dependencia_responsable: str = Field(
        description="Organismo de la CDMX responsable de atender (ej. 'SACMEX', 'SOBSE', 'Alcaldía Local', 'SEMOVI', 'C5')"
    )
    prioridad: str = Field(
        description="Nivel de severidad: 'Baja', 'Media', 'Alta' o 'Emergencia / Crítica'"
    )
    evaluacion_riesgo: str = Field(
        description="Riesgo para transeúntes, vehículos o infraestructura"
    )
    descripcion_tecnica: str = Field(
        description="Descripción técnica objetiva para la cuadrilla operativa"
    )
    accion_inmediata_cuadrilla: str = Field(
        description="Instrucción de primera respuesta para los técnicos en campo"
    )
    tiempo_estimado_atencion: str = Field(
        description="Ventana estimada de atención según el catálogo de servicios (ej. '24 horas', '72 horas', '5 días hábiles')"
    )
    notificacion_ciudadano_whatsapp: str = Field(
        description="Texto empático y conciso en español para notificar al vecino por WhatsApp"
    )


SYSTEM_PROMPT = """Eres el Inspector de Infraestructura Urbana con IA para el SUAC
(Sistema Unificado de Atención Ciudadana) de la Ciudad de México (CDMX).

Tu tarea es analizar la fotografía enviada por el ciudadano y clasificar el problema urbano:
1. Mapea el incidente al catálogo oficial del Gobierno de la CDMX (SACMEX para fugas/drenaje,
   SOBSE para avenidas primarias/puentes, Alcaldías para vías secundarias/baches/basura/luminarias locales).
2. Evalúa objetivamente la severidad y el riesgo civil.
3. Redacta una descripción técnica precisa para la cuadrilla y un mensaje claro para el ciudadano.

REGLAS CRÍTICAS DE CLASIFICACIÓN EN CDMX:
- BACHE / SOCAVÓN vs FUGA DE AGUA:
  * Si la imagen muestra asfalto fracturado, hundimientos, hoyos, escombros, cascajo, tierra o grava expuesta en el pavimento, clasifícalo como 'Mantenimiento Asfáltico (Bacheo)' para la Alcaldía (calles locales) o SOBSE (vías primarias).
  * La presencia de coladeras, alcantarillas o rejillas pluviales cercanas NO significa fuga de agua.
  * Para clasificar como 'Agua Potable y Drenaje / Fuga de Agua' (SACMEX), DEBE haber evidencia visible de agua activa, encharcamiento abundante o flujo continuo de agua brotando de una tubería.

Devuelve el resultado estrictamente estructurado según el esquema JSON solicitado."""

SAMPLE_REPORTS = [
    {
        "categoria_suac": "Agua Potable y Drenaje",
        "subcategoria": "Fuga de agua potable en banqueta/arroyo vehicular",
        "dependencia_responsable": "SACMEX",
        "prioridad": "Alta",
        "evaluacion_riesgo": "Pérdida de recurso hídrico crítico y riesgo de reblandecimiento de subsuelo con formación de socavón.",
        "descripcion_tecnica": "Afloramiento continuo de agua limpia con caudal moderado a fuerte proveniente de fractura en toma secundaria domiciliaria bajo la banqueta. Afecta arroyo vehicular en carril derecho.",
        "accion_inmediata_cuadrilla": "Despliegue de cuadrilla SACMEX para cierre de válvula de paso, rotura controlada de concreto y sustitución de niple/tubería.",
        "tiempo_estimado_atencion": "24 a 48 horas",
        "notificacion_ciudadano_whatsapp": "¡Hola! Registramos tu reporte de fuga de agua. Folio {folio}. La brigada de SACMEX ya fue notificada para su reparación prioritaria. ¡Gracias por cuidar el agua de nuestra ciudad!"
    },
    {
        "categoria_suac": "Mantenimiento Asfáltico",
        "subcategoria": "Bache profundo en vía secundaria",
        "dependencia_responsable": "Alcaldía Local",
        "prioridad": "Media",
        "evaluacion_riesgo": "Riesgo de daño severo a suspensión de automóviles, ponchadura de llantas y caída de ciclistas/motociclistas.",
        "descripcion_tecnica": "Depresión asfáltica de aprox. 60 cm de diámetro y 12 cm de profundidad con bordes angulares expuestos. Desgaste severo de la capa de rodamiento sin daño estructural aparente a tuberías subterráneas.",
        "accion_inmediata_cuadrilla": "Limpieza de escombros, aplicación de emulsión asfáltica y relleno compactado en frío/caliente.",
        "tiempo_estimado_atencion": "3 a 5 días hábiles",
        "notificacion_ciudadano_whatsapp": "¡Hola! Tu reporte de bacheo quedó registrado con el folio {folio}. Se turnó al área de servicios urbanos de tu Alcaldía para su programación en cuadrilla."
    },
    {
        "categoria_suac": "Limpia y Manejo de Residuos Sólidos",
        "subcategoria": "Tiradero clandestino en esquina/espacio público",
        "dependencia_responsable": "Alcaldía Local",
        "prioridad": "Media",
        "evaluacion_riesgo": "Foco de infección, proliferación de fauna nociva y taponamiento de coladeras pluviales ante lluvias.",
        "descripcion_tecnica": "Acumulación irregular de residuos mixtos (inorgánicos, cascajo y bolsas plásticas) en esquina peatonal ocupando aprox. 4 metros cuadrados.",
        "accion_inmediata_cuadrilla": "Despacho de camión recolector de limpia y lavado de superficie con desinfectante.",
        "tiempo_estimado_atencion": "24 a 72 horas",
        "notificacion_ciudadano_whatsapp": "¡Hola! Recibimos el reporte del tiradero clandestino (Folio {folio}). El equipo de limpia de tu alcaldía programó el retiro para sanear la zona."
    }
]


def generate_folio() -> str:
    return f"SUAC-2026-{random.randint(100000, 999999)}"


def analyze_image_with_ai(image: Image.Image) -> SUACReport:
    """Analiza la imagen utilizando la API de Gemini con lista de respaldo automático."""
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_GENAI_API_KEY")
    )
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key
        os.environ["GOOGLE_API_KEY"] = api_key

    if not api_key:
        console.print("[yellow]⚠️  GEMINI_API_KEY no detectada. Operando en Modo Simulación / Demo...[/yellow]")
        template = random.choice(SAMPLE_REPORTS)
        folio = generate_folio()
        return SUACReport(
            folio_simulado=folio,
            categoria_suac=template["categoria_suac"],
            subcategoria=template["subcategoria"],
            dependencia_responsable=template["dependencia_responsable"],
            prioridad=template["prioridad"],
            evaluacion_riesgo=template["evaluacion_riesgo"],
            descripcion_tecnica=template["descripcion_tecnica"],
            accion_inmediata_cuadrilla=template["accion_inmediata_cuadrilla"],
            tiempo_estimado_atencion=template["tiempo_estimado_atencion"],
            notificacion_ciudadano_whatsapp=template["notificacion_ciudadano_whatsapp"].format(folio=folio),
        )

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        image_bytes = buffered.getvalue()

        # Cadena de modelos resilientes
        preferred_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        candidate_models = [
            preferred_model,
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.1-flash-lite",
        ]
        candidate_models = list(dict.fromkeys(candidate_models))

        prompt = (
            "Analiza detalladamente esta fotografía tomada en una calle de la Ciudad de México. "
            "Detecta la falla urbana visible, su gravedad, la dependencia competente y genera el reporte formal para el SUAC."
        )

        last_err = None
        for model_name in candidate_models:
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=[
                        types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                        prompt
                    ],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=SUACReport,
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.2,
                    )
                )

                report_dict = json.loads(response.text)
                if not report_dict.get("folio_simulado"):
                    report_dict["folio_simulado"] = generate_folio()
                return SUACReport(**report_dict)

            except Exception as model_err:
                last_err = model_err
                console.print(f"[yellow]Aviso: Modelo {model_name} no disponible ({model_err}). Probando siguiente...[/yellow]")
                continue

        raise last_err or RuntimeError("Ningún modelo multimodal estuvo disponible.")

    except Exception as e:
        console.print(f"[red]Error al contactar la API de Gemini: {e}. Usando catálogo fallback.[/red]")
        template = random.choice(SAMPLE_REPORTS)
        folio = generate_folio()
        return SUACReport(
            folio_simulado=f"{folio}-FALLBACK",
            categoria_suac=template["categoria_suac"],
            subcategoria=f"{template['subcategoria']} (Aviso: Modo sin conexión)",
            dependencia_responsable=template["dependencia_responsable"],
            prioridad=template["prioridad"],
            evaluacion_riesgo=template["evaluacion_riesgo"],
            descripcion_tecnica=f"Nota de sistema: {e}. {template['descripcion_tecnica']}",
            accion_inmediata_cuadrilla=template["accion_inmediata_cuadrilla"],
            tiempo_estimado_atencion=template["tiempo_estimado_atencion"],
            notificacion_ciudadano_whatsapp=template["notificacion_ciudadano_whatsapp"].format(folio=folio),
        )


# ==============================================================================
# 3. HTML Templates (Portal Ciudadano & Portal Admin)
# ==============================================================================
CITIZEN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Reporta CDMX — Inspector Comunitario de Infraestructura con IA</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --cdmx-green: #006847;
            --cdmx-wine: #9f2241;
            --cdmx-dark: #1f2937;
            --cdmx-light: #f3f4f6;
            --cdmx-card: #ffffff;
            --cdmx-border: #e5e7eb;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: #f8fafc;
            color: var(--cdmx-dark);
            min-height: 100vh;
            padding: 20px 16px;
        }
        .container { max-width: 840px; margin: 0 auto; }
        .top-nav {
            display: flex;
            justify-content: flex-end;
            margin-bottom: 12px;
        }
        .btn-nav-admin {
            background: white;
            color: var(--cdmx-dark);
            text-decoration: none;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.84rem;
            font-weight: 600;
            border: 1px solid var(--cdmx-border);
            display: inline-flex;
            align-items: center;
            gap: 6px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            transition: all 0.2s;
        }
        .btn-nav-admin:hover {
            border-color: var(--cdmx-green);
            color: var(--cdmx-green);
        }
        header {
            background: linear-gradient(135deg, var(--cdmx-green) 0%, #004d34 100%);
            color: white;
            padding: 26px 22px;
            border-radius: 16px;
            box-shadow: 0 10px 25px -5px rgba(0, 104, 71, 0.2);
            margin-bottom: 22px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .header-title h1 { font-size: 1.55rem; font-weight: 800; letter-spacing: -0.02em; }
        .header-title p { font-size: 0.9rem; opacity: 0.9; margin-top: 4px; }
        .badge-gov {
            background: rgba(255, 255, 255, 0.2);
            padding: 6px 12px;
            border-radius: 20px;
            font-size: 0.78rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .card {
            background: var(--cdmx-card);
            border: 1px solid var(--cdmx-border);
            border-radius: 16px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        }
        .upload-area {
            border: 2px dashed #cbd5e1;
            border-radius: 12px;
            padding: 30px 16px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            background: #f8fafc;
        }
        .upload-area:hover { border-color: var(--cdmx-green); background: #f0fdf4; }
        .upload-icon { font-size: 42px; margin-bottom: 10px; display: block; }
        .action-buttons {
            display: flex;
            gap: 12px;
            justify-content: center;
            flex-wrap: wrap;
            margin-top: 14px;
        }
        .btn-camera {
            background: var(--cdmx-wine);
            color: white;
            border: none;
            padding: 12px 22px;
            font-size: 0.95rem;
            font-weight: 600;
            border-radius: 10px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: opacity 0.2s;
        }
        .btn-gallery {
            background: #1e293b;
            color: white;
            border: none;
            padding: 12px 22px;
            font-size: 0.95rem;
            font-weight: 600;
            border-radius: 10px;
            cursor: pointer;
            display: inline-flex;
            align-items: center;
            gap: 8px;
            transition: opacity 0.2s;
        }
        .btn-camera:hover, .btn-gallery:hover { opacity: 0.9; }
        .btn-submit {
            background: var(--cdmx-green);
            color: white;
            width: 100%;
            border: none;
            padding: 14px;
            font-size: 1.05rem;
            font-weight: 700;
            border-radius: 10px;
            cursor: pointer;
            margin-top: 16px;
            display: none;
            transition: all 0.2s;
        }
        .btn-submit:hover { background: #004d34; }
        .preview-box { display: none; margin-top: 16px; text-align: center; }
        .preview-box img {
            max-width: 100%;
            max-height: 300px;
            border-radius: 10px;
            object-fit: cover;
            border: 2px solid var(--cdmx-border);
        }
        .quick-demos {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
            margin-top: 14px;
            align-items: center;
        }
        .demo-btn {
            background: white;
            border: 1px solid var(--cdmx-border);
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 0.8rem;
            cursor: pointer;
            transition: all 0.2s;
        }
        .demo-btn:hover { border-color: var(--cdmx-green); color: var(--cdmx-green); }
        .loading {
            display: none;
            text-align: center;
            padding: 24px;
            font-weight: 600;
            color: var(--cdmx-green);
        }
        .spinner {
            width: 32px;
            height: 32px;
            border: 4px solid #cbd5e1;
            border-top: 4px solid var(--cdmx-green);
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
            margin: 0 auto 12px auto;
        }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .report-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--cdmx-light);
            padding-bottom: 12px;
            margin-bottom: 16px;
        }
        .folio-tag {
            font-family: monospace;
            font-size: 1.1rem;
            font-weight: 700;
            color: var(--cdmx-green);
            background: #dcfce7;
            padding: 4px 10px;
            border-radius: 6px;
        }
        .priority-badge {
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 0.82rem;
            font-weight: 700;
            text-transform: uppercase;
        }
        .p-Alta, .p-Emergencia { background: #fee2e2; color: #b91c1c; }
        .p-Media { background: #fef3c7; color: #b45309; }
        .p-Baja { background: #e0f2fe; color: #0369a1; }
        .meta-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
            margin-bottom: 16px;
        }
        .meta-item {
            background: #f8fafc;
            padding: 12px;
            border-radius: 8px;
            border-left: 3px solid var(--cdmx-green);
        }
        .meta-item label {
            display: block;
            font-size: 0.72rem;
            color: #64748b;
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 2px;
        }
        .meta-item span { font-size: 0.95rem; font-weight: 600; }
        .section-box {
            background: #f8fafc;
            padding: 14px;
            border-radius: 8px;
            margin-bottom: 12px;
        }
        .section-box h4 {
            font-size: 0.8rem;
            text-transform: uppercase;
            color: #475569;
            margin-bottom: 6px;
        }
        .section-box p { font-size: 0.92rem; line-height: 1.5; }
        .whatsapp-preview {
            background: #e7f8ef;
            border: 1px solid #bbf7d0;
            padding: 14px;
            border-radius: 10px;
            font-size: 0.92rem;
            color: #14532d;
            margin-top: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="top-nav">
            <a href="/admin" class="btn-nav-admin">
                <span>🏛️ Ir al Panel de Despacho (/admin)</span>
            </a>
        </div>

        <header>
            <div class="header-title">
                <h1>📍 Reporta CDMX</h1>
                <p>Inspector Comunitario de Infraestructura Urbana con IA</p>
            </div>
            <div class="badge-gov">SUAC / Locatel</div>
        </header>

        <div class="card">
            <div class="upload-area" id="dropArea" onclick="document.getElementById('galleryInput').click()">
                <span class="upload-icon">📸</span>
                <h3>Toma una fotografía o sube una imagen</h3>
                <p style="color: #64748b; font-size: 0.88rem; margin-top: 4px;">
                    Arrastra una foto aquí o elige una opción:
                </p>
                <div class="action-buttons" onclick="event.stopPropagation()">
                    <button class="btn-camera" type="button" onclick="document.getElementById('cameraInput').click()">
                        <span>📷 Tomar Foto</span>
                    </button>
                    <button class="btn-gallery" type="button" onclick="document.getElementById('galleryInput').click()">
                        <span>🖼️ Elegir de Galería / Archivo</span>
                    </button>
                </div>
            </div>
            <input type="file" id="cameraInput" accept="image/*" capture="environment" style="display: none;" onchange="handleFileSelect(event)">
            <input type="file" id="galleryInput" accept="image/*" style="display: none;" onchange="handleFileSelect(event)">
            
            <div class="quick-demos">
                <span style="font-size: 0.78rem; font-weight: 600; color: #64748b;">Pruebas rápidas:</span>
                <button class="demo-btn" onclick="simulateTest('fuga')">💧 Fuga de Agua</button>
                <button class="demo-btn" onclick="simulateTest('bache')">🕳️ Bache Profundo</button>
                <button class="demo-btn" onclick="simulateTest('basura')">🗑️ Tiradero</button>
            </div>

            <div class="preview-box" id="previewBox">
                <img id="previewImg" src="" alt="Vista previa">
            </div>

            <button class="btn-submit" id="btnSubmit" onclick="submitAnalysis()">
                ✨ Analizar y Registrar Reporte SUAC
            </button>
        </div>

        <div class="loading" id="loadingBox">
            <div class="spinner"></div>
            Analizando imagen con visión multimodal y registrando en la base de datos...
        </div>

        <div class="card" id="reportResult" style="display: none;">
            <div class="report-header">
                <div>
                    <span style="font-size: 0.75rem; color: #64748b; display: block;">FOLIO OFICIAL REGISTRADO</span>
                    <span class="folio-tag" id="rFolio"></span>
                </div>
                <div class="priority-badge" id="rPrioridad"></div>
            </div>

            <div class="meta-grid">
                <div class="meta-item">
                    <label>Categoría SUAC</label>
                    <span id="rCategoria"></span>
                </div>
                <div class="meta-item">
                    <label>Dependencia Asignada</label>
                    <span id="rDependencia" style="color: var(--cdmx-green);"></span>
                </div>
                <div class="meta-item">
                    <label>Tiempo Est. Atención</label>
                    <span id="rTiempo"></span>
                </div>
                <div class="meta-item">
                    <label>Subcategoría</label>
                    <span id="rSubcategoria"></span>
                </div>
            </div>

            <div class="section-box">
                <h4>Evaluación de Riesgo a la Ciudadanía</h4>
                <p id="rRiesgo"></p>
            </div>

            <div class="section-box">
                <h4>Descripción Técnica para Cuadrilla Operativa</h4>
                <p id="rDescripcion"></p>
            </div>

            <div class="section-box">
                <h4>Acción Inmediata Recomendada</h4>
                <p id="rAccion"></p>
            </div>

            <div class="whatsapp-preview">
                <strong>📲 Mensaje Automatizado para WhatsApp:</strong>
                <p id="rWhatsapp" style="margin-top: 4px;"></p>
            </div>
            
            <div style="margin-top: 16px; text-align: right;">
                <a href="/admin" style="color: var(--cdmx-green); font-size: 0.88rem; font-weight: 600; text-decoration: none;">
                    👉 Ver reporte en la Mesa de Control (/admin) &rarr;
                </a>
            </div>
        </div>
    </div>

    <script>
        let currentImageBase64 = null;

        function processFile(file) {
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(event) {
                currentImageBase64 = event.target.result;
                document.getElementById('previewImg').src = currentImageBase64;
                document.getElementById('previewBox').style.display = 'block';
                document.getElementById('btnSubmit').style.display = 'block';
                document.getElementById('btnSubmit').scrollIntoView({ behavior: 'smooth' });
            };
            reader.readAsDataURL(file);
        }

        function handleFileSelect(e) {
            const file = e.target.files[0];
            processFile(file);
            e.target.value = '';
        }

        const dropArea = document.getElementById('dropArea');
        ['dragenter', 'dragover'].forEach(eventName => {
            dropArea.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropArea.style.borderColor = 'var(--cdmx-green)';
                dropArea.style.background = '#f0fdf4';
            }, false);
        });
        ['dragleave', 'drop'].forEach(eventName => {
            dropArea.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropArea.style.borderColor = '#cbd5e1';
                dropArea.style.background = '#f8fafc';
            }, false);
        });
        dropArea.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            const files = dt.files;
            if (files && files.length > 0) {
                processFile(files[0]);
            }
        }, false);

        async function submitAnalysis() {
            if (!currentImageBase64) return;
            document.getElementById('loadingBox').style.display = 'block';
            document.getElementById('reportResult').style.display = 'none';

            try {
                const response = await fetch('/api/analyze', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ image_data: currentImageBase64 })
                });
                const data = await response.json();
                renderReport(data);
            } catch (err) {
                alert('Error al generar el reporte: ' + err);
            } finally {
                document.getElementById('loadingBox').style.display = 'none';
            }
        }

        async function simulateTest(type) {
            document.getElementById('loadingBox').style.display = 'block';
            document.getElementById('reportResult').style.display = 'none';
            try {
                const response = await fetch('/api/simulate?type=' + type);
                const data = await response.json();
                renderReport(data);
            } finally {
                document.getElementById('loadingBox').style.display = 'none';
            }
        }

        function renderReport(report) {
            document.getElementById('rFolio').textContent = report.folio_simulado;
            const pBadge = document.getElementById('rPrioridad');
            pBadge.textContent = 'Prioridad: ' + report.prioridad;
            pBadge.className = 'priority-badge p-' + report.prioridad.split(' ')[0];
            document.getElementById('rCategoria').textContent = report.categoria_suac;
            document.getElementById('rSubcategoria').textContent = report.subcategoria;
            document.getElementById('rDependencia').textContent = report.dependencia_responsable;
            document.getElementById('rTiempo').textContent = report.tiempo_estimado_atencion;
            document.getElementById('rRiesgo').textContent = report.evaluacion_riesgo;
            document.getElementById('rDescripcion').textContent = report.descripcion_tecnica;
            document.getElementById('rAccion').textContent = report.accion_inmediata_cuadrilla;
            document.getElementById('rWhatsapp').textContent = report.notificacion_ciudadano_whatsapp;
            document.getElementById('reportResult').style.display = 'block';
            document.getElementById('reportResult').scrollIntoView({ behavior: 'smooth' });
        }
    </script>
</body>
</html>
"""


ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Mesa de Control SUAC CDMX — Despacho de Cuadrillas con IA</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --cdmx-green: #006847;
            --cdmx-wine: #9f2241;
            --cdmx-dark: #0f172a;
            --cdmx-card: #ffffff;
            --cdmx-border: #e2e8f0;
            --cdmx-bg: #f8fafc;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--cdmx-bg);
            color: var(--cdmx-dark);
            min-height: 100vh;
        }
        /* Top Institutional Bar */
        .admin-nav {
            background: #111827;
            color: white;
            padding: 14px 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 3px solid var(--cdmx-wine);
        }
        .brand {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .brand-icon {
            background: var(--cdmx-green);
            color: white;
            font-size: 1.1rem;
            padding: 6px 10px;
            border-radius: 8px;
            font-weight: 800;
        }
        .brand-text h1 { font-size: 1.15rem; font-weight: 800; }
        .brand-text p { font-size: 0.76rem; color: #94a3b8; }
        .nav-actions {
            display: flex;
            gap: 12px;
            align-items: center;
        }
        .btn-citizen {
            background: #1e293b;
            color: white;
            text-decoration: none;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            transition: all 0.2s;
            border: 1px solid #334155;
        }
        .btn-citizen:hover { background: #334155; }
        .btn-export {
            background: var(--cdmx-green);
            color: white;
            text-decoration: none;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            transition: opacity 0.2s;
        }
        .btn-export:hover { opacity: 0.9; }

        .container {
            max-width: 1280px;
            margin: 0 auto;
            padding: 24px 20px;
        }

        /* KPI Cards */
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .kpi-card {
            background: var(--cdmx-card);
            border: 1px solid var(--cdmx-border);
            border-radius: 12px;
            padding: 18px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .kpi-card .val {
            font-size: 2rem;
            font-weight: 800;
            line-height: 1;
            margin-top: 4px;
        }
        .kpi-card .lbl {
            font-size: 0.78rem;
            text-transform: uppercase;
            font-weight: 700;
            color: #64748b;
        }
        .kpi-icon {
            font-size: 2rem;
            opacity: 0.85;
        }

        /* Filters Bar */
        .filters-panel {
            background: var(--cdmx-card);
            border: 1px solid var(--cdmx-border);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 20px;
            display: flex;
            gap: 16px;
            align-items: center;
            flex-wrap: wrap;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        .filter-group {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .filter-group label {
            font-size: 0.82rem;
            font-weight: 700;
            color: #475569;
            text-transform: uppercase;
        }
        .filter-group select {
            padding: 8px 12px;
            border-radius: 8px;
            border: 1px solid var(--cdmx-border);
            font-size: 0.88rem;
            background: white;
            font-weight: 600;
            color: var(--cdmx-dark);
            cursor: pointer;
        }
        .btn-refresh {
            margin-left: auto;
            background: white;
            border: 1px solid var(--cdmx-border);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 0.85rem;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }
        .btn-refresh:hover { background: #f1f5f9; }

        /* Incident Table */
        .table-card {
            background: var(--cdmx-card);
            border: 1px solid var(--cdmx-border);
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        }
        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.9rem;
        }
        thead {
            background: #f8fafc;
            border-bottom: 2px solid var(--cdmx-border);
        }
        th {
            padding: 14px 16px;
            font-size: 0.76rem;
            text-transform: uppercase;
            font-weight: 700;
            color: #475569;
            letter-spacing: 0.04em;
        }
        td {
            padding: 16px;
            border-bottom: 1px solid var(--cdmx-border);
            vertical-align: top;
        }
        tr:hover { background: #f8fafc; }
        .folio-link {
            font-family: monospace;
            font-weight: 700;
            color: var(--cdmx-green);
            background: #dcfce7;
            padding: 3px 8px;
            border-radius: 6px;
            display: inline-block;
            font-size: 0.82rem;
        }
        .time-sub {
            display: block;
            font-size: 0.75rem;
            color: #64748b;
            margin-top: 4px;
        }
        .agency-badge {
            font-size: 0.78rem;
            font-weight: 700;
            padding: 4px 8px;
            border-radius: 6px;
            display: inline-block;
        }
        .agency-sacmex { background: #e0f2fe; color: #0369a1; }
        .agency-alcaldia { background: #ecfdf5; color: #047857; }
        .agency-sobse { background: #f3e8ff; color: #6b21a8; }
        
        .p-Alta, .p-Emergencia { background: #fee2e2; color: #991b1b; }
        .p-Media { background: #fef3c7; color: #92400e; }
        .p-Baja { background: #f1f5f9; color: #475569; }

        .status-select {
            padding: 6px 10px;
            border-radius: 8px;
            font-size: 0.82rem;
            font-weight: 700;
            border: 1px solid var(--cdmx-border);
            cursor: pointer;
            transition: all 0.2s;
        }
        .status-Pendiente { background: #fff7ed; color: #c2410c; border-color: #fed7aa; }
        .status-En-Cuadrilla { background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }
        .status-Atendido { background: #f0fdf4; color: #15803d; border-color: #bbf7d0; }

        .thumb-box {
            width: 72px;
            height: 72px;
            border-radius: 8px;
            overflow: hidden;
            background: #e2e8f0;
            cursor: pointer;
            border: 1px solid var(--cdmx-border);
        }
        .thumb-box img {
            width: 100%;
            height: 100%;
            object-fit: cover;
            transition: transform 0.2s;
        }
        .thumb-box:hover img { transform: scale(1.1); }
        .desc-text {
            font-size: 0.86rem;
            color: #334155;
            line-height: 1.4;
            max-width: 380px;
        }
        .desc-title {
            font-weight: 700;
            color: #0f172a;
            margin-bottom: 2px;
        }

        /* Modal Preview */
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7);
            align-items: center;
            justify-content: center;
            z-index: 100;
        }
        .modal img {
            max-width: 90vw;
            max-height: 85vh;
            border-radius: 12px;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
        }
    </style>
</head>
<body>
    <div class="admin-nav">
        <div class="brand">
            <span class="brand-icon">🏛️ CDMX</span>
            <div class="brand-text">
                <h1>Mesa de Control SUAC / Locatel</h1>
                <p>Centro de Despacho y Monitoreo de Cuadrillas con Inteligencia Artificial</p>
            </div>
        </div>
        <div class="nav-actions">
            <a href="/" class="btn-citizen">📱 Vista Ciudadana (/)</a>
            <a href="/api/export" class="btn-export">⬇️ Exportar CSV</a>
        </div>
    </div>

    <div class="container">
        <!-- KPI Summary Cards -->
        <div class="kpi-grid">
            <div class="kpi-card">
                <div>
                    <div class="lbl">Total Incidentes</div>
                    <div class="val" id="kpiTotal">0</div>
                </div>
                <div class="kpi-icon">📋</div>
            </div>
            <div class="kpi-card" style="border-left: 4px solid #0284c7;">
                <div>
                    <div class="lbl">Fugas SACMEX</div>
                    <div class="val" id="kpiSacmex" style="color: #0284c7;">0</div>
                </div>
                <div class="kpi-icon">💧</div>
            </div>
            <div class="kpi-card" style="border-left: 4px solid var(--cdmx-green);">
                <div>
                    <div class="lbl">Baches y Pavimento</div>
                    <div class="val" id="kpiBaches" style="color: var(--cdmx-green);">0</div>
                </div>
                <div class="kpi-icon">🕳️</div>
            </div>
            <div class="kpi-card" style="border-left: 4px solid #16a34a;">
                <div>
                    <div class="lbl">Resueltos / Atendidos</div>
                    <div class="val" id="kpiAtendidos" style="color: #16a34a;">0</div>
                </div>
                <div class="kpi-icon">✅</div>
            </div>
        </div>

        <!-- Filters Bar -->
        <div class="filters-panel">
            <div class="filter-group">
                <label>Dependencia:</label>
                <select id="filterDep" onchange="loadReports()">
                    <option value="Todos">Todas las Dependencias</option>
                    <option value="SACMEX">SACMEX (Agua y Drenaje)</option>
                    <option value="Alcaldía">Alcaldías (Vías secundarias/Limpia)</option>
                    <option value="SOBSE">SOBSE (Vías Primarias)</option>
                </select>
            </div>
            <div class="filter-group">
                <label>Estatus:</label>
                <select id="filterStatus" onchange="loadReports()">
                    <option value="Todos">Todos los Estatus</option>
                    <option value="Pendiente">Pendiente</option>
                    <option value="En Cuadrilla">En Cuadrilla</option>
                    <option value="Atendido">Atendido</option>
                </select>
            </div>
            <button class="btn-refresh" onclick="loadDashboard()">🔄 Actualizar Mesa</button>
        </div>

        <!-- Table View -->
        <div class="table-card">
            <table>
                <thead>
                    <tr>
                        <th>Evidencia</th>
                        <th>Folio & Registro</th>
                        <th>Problema & Diagnóstico IA</th>
                        <th>Dependencia</th>
                        <th>Prioridad</th>
                        <th>Estatus Operativo</th>
                    </tr>
                </thead>
                <tbody id="reportsTableBody">
                    <tr><td colspan="6" style="text-align: center; padding: 32px; color: #64748b;">Cargando incidencias registradas...</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <!-- Image Modal -->
    <div class="modal" id="imageModal" onclick="this.style.display='none'">
        <img id="modalImg" src="" alt="Evidencia">
    </div>

    <script>
        async function loadDashboard() {
            await Promise.all([loadKPIs(), loadReports()]);
        }

        async function loadKPIs() {
            try {
                const res = await fetch('/api/kpis');
                const data = await res.json();
                document.getElementById('kpiTotal').textContent = data.total;
                document.getElementById('kpiSacmex').textContent = data.sacmex;
                document.getElementById('kpiBaches').textContent = data.baches;
                document.getElementById('kpiAtendidos').textContent = data.atendidos;
            } catch (err) {
                console.error('Error al cargar KPIs:', err);
            }
        }

        async function loadReports() {
            const dep = document.getElementById('filterDep').value;
            const status = document.getElementById('filterStatus').value;
            const tbody = document.getElementById('reportsTableBody');

            try {
                const url = `/api/reports?dependencia=${encodeURIComponent(dep)}&estatus=${encodeURIComponent(status)}`;
                const res = await fetch(url);
                const reports = await res.json();

                if (!reports || reports.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 32px; color: #64748b;">No se encontraron reportes con los filtros seleccionados.</td></tr>';
                    return;
                }

                tbody.innerHTML = reports.map(r => {
                    const thumbUrl = r.image_filename ? `/uploads/${r.image_filename}` : '';
                    const thumbHtml = thumbUrl 
                        ? `<div class="thumb-box" onclick="showModal('${thumbUrl}')"><img src="${thumbUrl}" alt="Foto"></div>`
                        : `<div class="thumb-box" style="display:flex;align-items:center;justify-content:center;font-size:1.5rem;">📄</div>`;

                    const depClass = r.dependencia_responsable.includes('SACMEX') ? 'agency-sacmex' : (r.dependencia_responsable.includes('SOBSE') ? 'agency-sobse' : 'agency-alcaldia');
                    const priorityClass = 'p-' + r.prioridad.split(' ')[0];
                    const statusClass = 'status-' + r.estatus.replace(/\s+/g, '-');

                    return `
                        <tr>
                            <td>${thumbHtml}</td>
                            <td>
                                <span class="folio-link">${r.folio}</span>
                                <span class="time-sub">📅 ${r.created_at || 'Hoy'}</span>
                                <span class="time-sub">⏱️ SLA: ${r.tiempo_estimado}</span>
                            </td>
                            <td>
                                <div class="desc-text">
                                    <div class="desc-title">${r.subcategoria}</div>
                                    <p>${r.descripcion_tecnica}</p>
                                    <div style="margin-top: 6px; font-size: 0.78rem; color: #b91c1c;">
                                        <strong>⚠️ Riesgo:</strong> ${r.evaluacion_riesgo}
                                    </div>
                                </div>
                            </td>
                            <td>
                                <span class="agency-badge ${depClass}">${r.dependencia_responsable}</span>
                            </td>
                            <td>
                                <span class="agency-badge ${priorityClass}">${r.prioridad}</span>
                            </td>
                            <td>
                                <select class="status-select ${statusClass}" onchange="changeStatus('${r.folio}', this.value, this)">
                                    <option value="Pendiente" ${r.estatus === 'Pendiente' ? 'selected' : ''}>⏳ Pendiente</option>
                                    <option value="En Cuadrilla" ${r.estatus === 'En Cuadrilla' ? 'selected' : ''}>🚜 En Cuadrilla</option>
                                    <option value="Atendido" ${r.estatus === 'Atendido' ? 'selected' : ''}>✅ Atendido</option>
                                </select>
                            </td>
                        </tr>
                    `;
                }).join('');

            } catch (err) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align: center; padding: 32px; color: #b91c1c;">Error al consultar la base de datos.</td></tr>';
            }
        }

        async function changeStatus(folio, newStatus, selectElem) {
            try {
                const res = await fetch('/api/status', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ folio: folio, status: newStatus })
                });
                if (res.ok) {
                    selectElem.className = 'status-select status-' + newStatus.replace(/\s+/g, '-');
                    loadKPIs();
                } else {
                    alert('No se pudo actualizar el estatus');
                }
            } catch (err) {
                alert('Error de conexión: ' + err);
            }
        }

        function showModal(src) {
            document.getElementById('modalImg').src = src;
            document.getElementById('imageModal').style.display = 'flex';
        }

        // Cargar datos al iniciar
        loadDashboard();
    </script>
</body>
</html>
"""


# ==============================================================================
# 4. Servidor HTTP Multi-Portal
# ==============================================================================
class ReportaCDMXHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Portal Ciudadano
        if path == "/" or path == "/index.html":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(CITIZEN_HTML.encode("utf-8"))

        # 2. Portal de Administración
        elif path == "/admin":
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(ADMIN_HTML.encode("utf-8"))

        # 3. Servir imágenes de uploads/
        elif path.startswith("/uploads/"):
            filename = path.replace("/uploads/", "")
            file_path = UPLOADS_DIR / filename
            if file_path.exists() and file_path.is_file():
                ext = file_path.suffix.lower()
                mime = "image/jpeg"
                if ext == ".png":
                    mime = "image/png"
                elif ext == ".webp":
                    mime = "image/webp"

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
            else:
                self.send_response(HTTPStatus.NOT_FOUND)
                self.end_headers()

        # 4. API: Lista de reportes
        elif path == "/api/reports":
            dep = query.get("dependencia", ["Todos"])[0]
            status = query.get("estatus", ["Todos"])[0]
            reports = get_all_reports(dependencia=dep, estatus=status)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(reports, default=str).encode("utf-8"))

        # 5. API: KPIs
        elif path == "/api/kpis":
            kpis = get_kpis()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(kpis).encode("utf-8"))

        # 6. API: Exportar CSV
        elif path == "/api/export":
            reports = get_all_reports()
            output = io.StringIO()
            if reports:
                writer = csv.DictWriter(output, fieldnames=reports[0].keys())
                writer.writeheader()
                writer.writerows(reports)
            
            csv_data = output.getvalue()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition", "attachment; filename=reportes_suac_cdmx.csv")
            self.end_headers()
            self.wfile.write(csv_data.encode("utf-8"))

        # 7. API: Simular prueba rápida (guarda en BD)
        elif path.startswith("/api/simulate"):
            q = query.get("type", ["fuga"])[0]
            idx = 0 if "fuga" in q else (1 if "bache" in q else 2)
            template = SAMPLE_REPORTS[idx]
            folio = generate_folio()
            
            # Copiar imagen de ejemplo si existe
            sample_img_name = None
            if idx == 0 and (EXAMPLES_DIR / "fuga.jpg").exists():
                sample_img_name = f"{folio}.jpg"
                shutil.copy(EXAMPLES_DIR / "fuga.jpg", UPLOADS_DIR / sample_img_name)
            elif idx == 1 and (EXAMPLES_DIR / "bache.webp").exists():
                sample_img_name = f"{folio}.webp"
                shutil.copy(EXAMPLES_DIR / "bache.webp", UPLOADS_DIR / sample_img_name)

            rep = SUACReport(
                folio_simulado=folio,
                categoria_suac=template["categoria_suac"],
                subcategoria=template["subcategoria"],
                dependencia_responsable=template["dependencia_responsable"],
                prioridad=template["prioridad"],
                evaluacion_riesgo=template["evaluacion_riesgo"],
                descripcion_tecnica=template["descripcion_tecnica"],
                accion_inmediata_cuadrilla=template["accion_inmediata_cuadrilla"],
                tiempo_estimado_atencion=template["tiempo_estimado_atencion"],
                notificacion_ciudadano_whatsapp=template["notificacion_ciudadano_whatsapp"].format(folio=folio),
            )
            # Guardar en SQLite
            with get_db_connection() as conn:
                conn.execute("""
                    INSERT INTO reports (
                        folio, categoria_suac, subcategoria, dependencia_responsable,
                        prioridad, evaluacion_riesgo, descripcion_tecnica, accion_inmediata,
                        tiempo_estimado, mensaje_whatsapp, image_filename, estatus
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    rep.folio_simulado, rep.categoria_suac, rep.subcategoria,
                    rep.dependencia_responsable, rep.prioridad, rep.evaluacion_riesgo,
                    rep.descripcion_tecnica, rep.accion_inmediata_cuadrilla,
                    rep.tiempo_estimado_atencion, rep.notificacion_ciudadano_whatsapp,
                    sample_img_name, "Pendiente"
                ))
                conn.commit()

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(rep.model_dump_json().encode("utf-8"))

        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # Analizar imagen del ciudadano
        if path == "/api/analyze":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))

            image_raw = data.get("image_data", "")
            ext = ".jpg"
            if "," in image_raw:
                header_part, image_raw = image_raw.split(",", 1)
                if "png" in header_part:
                    ext = ".png"
                elif "webp" in header_part:
                    ext = ".webp"

            image_bytes = base64.b64decode(image_raw)
            image = Image.open(io.BytesIO(image_bytes))

            # Ejecutar análisis con IA
            report = analyze_image_with_ai(image)

            # Guardar en SQLite y disco
            save_report_to_db(report, image_bytes=image_bytes, ext=ext)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(report.model_dump_json().encode("utf-8"))

        # Actualizar estatus desde el panel admin
        elif path == "/api/status":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            folio = data.get("folio")
            status = data.get("status")

            if folio and status:
                ok = update_report_status(folio, status)
                self.send_response(HTTPStatus.OK if ok else HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": ok}).encode("utf-8"))
            else:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.end_headers()
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()


def run_web_server(port: int = 8000):
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), ReportaCDMXHandler)
    url_citizen = f"http://localhost:{port}"
    url_admin = f"http://localhost:{port}/admin"

    console.print(f"\n[bold green]🚀 Servidor Reporta CDMX en línea[/bold green]")
    console.print(f"📱 Portal Ciudadano:    [bold cyan]{url_citizen}[/bold cyan]")
    console.print(f"🏛️ Mesa de Control Admin: [bold yellow]{url_admin}[/bold yellow]")
    console.print(f"💾 Base de datos SQLite: [dim]{DB_PATH.name}[/dim]\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Servidor detenido por el usuario.[/yellow]")
        server.server_close()


# ==============================================================================
# 5. Punto de Entrada Principal (CLI + Web)
# ==============================================================================
def display_terminal_report(report: SUACReport, image_path: Optional[str] = None):
    title = f"📋 REPORTE DE INCIDENCIA CIUDADANA — {report.folio_simulado}"
    table = Table(title=title, show_header=True, header_style="bold green")
    table.add_column("Campo", style="bold cyan", width=26)
    table.add_column("Detalle Oficial SUAC CDMX", style="white")

    table.add_row("Folio SUAC", report.folio_simulado)
    table.add_row("Categoría", report.categoria_suac)
    table.add_row("Subcategoría", report.subcategoria)
    table.add_row("Dependencia Asignada", f"[bold yellow]{report.dependencia_responsable}[/bold yellow]")
    
    color_priority = "red" if "Alta" in report.prioridad or "Emergencia" in report.prioridad else "yellow"
    table.add_row("Prioridad / Severidad", f"[{color_priority}]{report.prioridad}[/{color_priority}]")
    table.add_row("Tiempo Est. Atención", report.tiempo_estimado_atencion)
    table.add_row("Riesgo a la Población", report.evaluacion_riesgo)
    table.add_row("Acción para Cuadrilla", report.accion_inmediata_cuadrilla)

    console.print()
    console.print(table)

    console.print(Panel(
        f"[italic]{report.descripcion_tecnica}[/italic]",
        title="[bold green]Descripción Técnica para Cuadrilla[/bold green]",
        border_style="green"
    ))

    console.print(Panel(
        f"📱 [green]{report.notificacion_ciudadano_whatsapp}[/green]",
        title="[bold blue]Mensaje Automatizado para WhatsApp del Ciudadano[/bold blue]",
        border_style="blue"
    ))


def main():
    parser = argparse.ArgumentParser(
        description="Reporta CDMX — Inspector Comunitario de Infraestructura Urbana con IA (SUAC/Locatel)"
    )
    parser.add_argument(
        "image",
        nargs="?",
        help="Ruta local a una fotografía de la falla urbana (ej. bache.jpg). Si se omite, lanza el servidor web.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Puerto para la interfaz web (por defecto: 8000)",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Forzar el lanzamiento del servidor web interactivo.",
    )

    args = parser.parse_args()

    # Si se pasa una imagen por CLI:
    if args.image and not args.serve:
        init_db()
        path = Path(args.image)
        if not path.exists():
            console.print(f"[red]Error: La imagen '{args.image}' no existe.[/red]")
            sys.exit(1)

        console.print(f"[bold green]Analizando imagen:[/bold green] {path.name}...")
        image = Image.open(path)
        report = analyze_image_with_ai(image)
        
        # Guardar en base de datos SQLite y carpeta uploads/
        image_bytes = path.read_bytes()
        save_report_to_db(report, image_bytes=image_bytes, ext=path.suffix)
        
        display_terminal_report(report, str(path))
        console.print(f"\n[green]✅ Reporte registrado en base de datos SQLite: {DB_PATH.name}[/green]")
    else:
        run_web_server(port=args.port)


if __name__ == "__main__":
    main()
