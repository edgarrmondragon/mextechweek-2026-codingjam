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
Analiza una fotografía de un problema en vía pública (fuga de agua, bache,
luminaria, tiradero de basura) y genera automáticamente el reporte oficial
para el SUAC (Sistema Unificado de Atención Ciudadana) de la Ciudad de México.

Ejecución:
  1. Con uv:
     uv run reporta_cdmx.py                # Inicia la interfaz web interactiva
     uv run reporta_cdmx.py bache.jpg      # Analiza desde terminal

  2. Con pipx:
     pipx run reporta_cdmx.py
     pipx run reporta_cdmx.py fuga.jpg

  3. Con Python estándar:
     python3 reporta_cdmx.py
"""

import argparse
import base64
import io
import json
import os
import random
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

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

# ==============================================================================
# 1. Esquema Pydantic del Reporte SUAC CDMX
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
        description="Riesgo para transeúntes, vehículos o infraestructura (ej. 'Riesgo de hundimiento/socavón por socavación hídrica')"
    )
    descripcion_tecnica: str = Field(
        description="Descripción técnica objetiva para la cuadrilla operativa, estimando dimensiones y afectación visible"
    )
    accion_inmediata_cuadrilla: str = Field(
        description="Instrucción de primera respuesta para los técnicos en campo"
    )
    tiempo_estimado_atencion: str = Field(
        description="Ventana estimada de atención según el catálogo de servicios (ej. '24 horas', '72 horas', '5 días hábiles')"
    )
    notificacion_ciudadano_whatsapp: str = Field(
        description="Texto empático y conciso en español para notificar al vecino por WhatsApp confirmando su reporte"
    )


# ==============================================================================
# 2. Catálogo y Motor de Análisis Multimodal
# ==============================================================================
SYSTEM_PROMPT = """Eres el Inspector de Infraestructura Urbana con IA para el SUAC
(Sistema Unificado de Atención Ciudadana) de la Ciudad de México (CDMX).

Tu tarea es analizar la fotografía enviada por el ciudadano y clasificar el problema urbano:
1. Mapea el incidente al catálogo oficial del Gobierno de la CDMX (SACMEX para fugas/drenaje,
   SOBSE para avenidas primarias/puentes, Alcaldías para vías secundarias/baches/basura/luminarias locales).
2. Evalúa objetivamente la severidad y el riesgo civil.
3. Redacta una descripción técnica precisa para la cuadrilla y un mensaje claro para el ciudadano.

REGLAS CRÍTICAS DE CLASIFICACIÓN EN CDMX:
- BACHE / SOCAVÓN vs FUGA DE AGUA:
  * Si la imagen muestra asfalto fracturado, hundimientos, hoyos, escombros, cascajo, tierra o grava expuesta en el pavimento, clasifícalo como 'Mantenimiento a la Carpeta Asfáltica (Bacheo)' para la Alcaldía (calles locales) o SOBSE (vías primarias).
  * La presencia de coladeras, alcantarillas o rejillas pluviales cercanas NO significa fuga de agua.
  * Para clasificar como 'Agua Potable y Drenaje / Fuga de Agua' (SACMEX), DEBE haber evidencia visible de agua activa, encharcamiento abundante o flujo continuo de agua brotando de una tubería.

Devuelve el resultado estrictamente estructurado según el esquema JSON solicitado."""

SAMPLE_REPORTS = [
    {
        "categoria_suac": "Agua Potable y Drenaje",
        "subcategoria": "Fuga de agua potable en banqueta/arroyo vehicular",
        "dependencia_responsable": "SACMEX (Sistema de Aguas de la CDMX)",
        "prioridad": "Alta",
        "evaluacion_riesgo": "Pérdida de recurso hídrico crítico y riesgo de reblandecimiento de subsuelo con formación de socavón.",
        "descripcion_tecnica": "Afloramiento continuo de agua limpia con caudal moderado a fuerte proveniente de fractura en toma secundaria domiciliaria bajo la banqueta. Afecta arroyo vehicular en carril derecho.",
        "accion_inmediata_cuadrilla": "Despliegue de cuadrilla SACMEX para cierre de válvula de paso, rotura controlada de concreto y sustitución de niple/tubería.",
        "tiempo_estimado_atencion": "24 a 48 horas",
        "notificacion_ciudadano_whatsapp": "¡Hola! Registramos tu reporte de fuga de agua. Folio {folio}. La brigada de SACMEX ya fue notificada para su reparación prioritaria. ¡Gracias por cuidar el agua de nuestra ciudad!"
    },
    {
        "categoria_suac": "Mantenimiento a la Carpeta Asfáltica (Bacheo)",
        "subcategoria": "Bache profundo en vía secundaria",
        "dependencia_responsable": "Alcaldía Local (Dirección General de Obras y Servicios Urbanos)",
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
        "dependencia_responsable": "Alcaldía Local (Servicios Públicos de Limpia)",
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
    """Analiza la imagen utilizando la API de Gemini (o modo de demostración si no hay clave)."""
    api_key = (
        os.environ.get("GEMINI_API_KEY")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GOOGLE_GENAI_API_KEY")
    )
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key
        os.environ["GOOGLE_API_KEY"] = api_key

    if not api_key:
        # Modo Demostración realista con folio oficial
        console.print("[yellow]⚠️  GEMINI_API_KEY no detectada en .env ni entorno. Generando reporte de demostración...[/yellow]")
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
        
        # Buffer en memoria para la imagen
        buffered = io.BytesIO()
        image.save(buffered, format="JPEG")
        image_bytes = buffered.getvalue()

        # Lista de modelos con fallback automático ante picos de demanda (503)
        preferred_model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        candidate_models = [
            preferred_model,
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.1-flash-lite",
        ]
        # Deduplicar preservando el orden
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
# 3. Presentación en Terminal (Rich)
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


# ==============================================================================
# 4. Servidor Web Integrado (Zero-Dependency UI)
# ==============================================================================
HTML_TEMPLATE = """<!DOCTYPE html>
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
            padding: 24px 16px;
        }
        .container {
            max-width: 820px;
            margin: 0 auto;
        }
        header {
            background: linear-gradient(135deg, var(--cdmx-green) 0%, #004d34 100%);
            color: white;
            padding: 28px 24px;
            border-radius: 16px;
            box-shadow: 0 10px 25px -5px rgba(0, 104, 71, 0.2);
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .header-title h1 {
            font-size: 1.6rem;
            font-weight: 800;
            letter-spacing: -0.02em;
        }
        .header-title p {
            font-size: 0.92rem;
            opacity: 0.9;
            margin-top: 4px;
        }
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
            padding: 32px 16px;
            text-align: center;
            cursor: pointer;
            transition: all 0.2s ease;
            background: #f8fafc;
        }
        .upload-area:hover {
            border-color: var(--cdmx-green);
            background: #f0fdf4;
        }
        .upload-icon {
            font-size: 42px;
            margin-bottom: 12px;
            display: block;
        }
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
        .preview-box {
            display: none;
            margin-top: 16px;
            text-align: center;
        }
        .preview-box img {
            max-width: 100%;
            max-height: 320px;
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
        .demo-btn:hover {
            border-color: var(--cdmx-green);
            color: var(--cdmx-green);
        }
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
        /* Report Result Styling */
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
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px;
            margin-bottom: 18px;
        }
        .meta-item {
            background: #f8fafc;
            padding: 12px;
            border-radius: 8px;
            border-left: 3px solid var(--cdmx-green);
        }
        .meta-item label {
            display: block;
            font-size: 0.75rem;
            color: #64748b;
            text-transform: uppercase;
            font-weight: 600;
            margin-bottom: 2px;
        }
        .meta-item span {
            font-size: 0.95rem;
            font-weight: 600;
        }
        .section-box {
            background: #f8fafc;
            padding: 14px;
            border-radius: 8px;
            margin-bottom: 12px;
        }
        .section-box h4 {
            font-size: 0.82rem;
            text-transform: uppercase;
            color: #475569;
            margin-bottom: 6px;
        }
        .section-box p {
            font-size: 0.92rem;
            line-height: 1.5;
        }
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
            <!-- Entrada con cámara directa en móviles -->
            <input type="file" id="cameraInput" accept="image/*" capture="environment" style="display: none;" onchange="handleFileSelect(event)">
            <!-- Entrada estándar para biblioteca de fotos y selector de archivos -->
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
                ✨ Analizar y Generar Reporte SUAC
            </button>
        </div>

        <div class="loading" id="loadingBox">
            <div class="spinner"></div>
            Analizando imagen con visión computacional y mapeando al catálogo oficial SUAC...
        </div>

        <div class="card" id="reportResult" style="display: none;">
            <div class="report-header">
                <div>
                    <span style="font-size: 0.8rem; color: #64748b; display: block;">FOLIO OFICIAL GENERADO</span>
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
                    <label>Dependencia Responsable</label>
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
            e.target.value = ''; // Permite volver a seleccionar el mismo archivo si es necesario
        }

        // Soporte para arrastrar y soltar (Drag and Drop)
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


class ReportaCDMXHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Reducir ruido de logs en consola
        return

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_TEMPLATE.encode("utf-8"))
        elif self.path.startswith("/api/simulate"):
            # Modo prueba rápida sin foto
            q = self.path.split("?type=")[-1] if "?type=" in self.path else "fuga"
            idx = 0 if "fuga" in q else (1 if "bache" in q else 2)
            template = SAMPLE_REPORTS[idx]
            folio = generate_folio()
            res = SUACReport(
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
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(res.model_dump_json().encode("utf-8"))
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()

    def do_POST(self):
        if self.path == "/api/analyze":
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            
            # Decodificar imagen base64
            image_raw = data.get("image_data", "")
            if "," in image_raw:
                image_raw = image_raw.split(",", 1)[1]
            image_bytes = base64.b64decode(image_raw)
            image = Image.open(io.BytesIO(image_bytes))

            # Ejecutar análisis
            report = analyze_image_with_ai(image)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(report.model_dump_json().encode("utf-8"))
        else:
            self.send_response(HTTPStatus.NOT_FOUND)
            self.end_headers()


def run_web_server(port: int = 8000):
    server = ThreadingHTTPServer(("0.0.0.0", port), ReportaCDMXHandler)
    url = f"http://localhost:{port}"
    console.print(f"\n[bold green]🚀 Reporta CDMX en línea[/bold green]")
    console.print(f"👉 Abre en tu navegador o celular: [bold cyan]{url}[/bold cyan]")
    console.print(f"[dim]Presiona Ctrl+C para detener el servidor.[/dim]\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Servidor detenido por el usuario.[/yellow]")
        server.server_close()


# ==============================================================================
# 5. Punto de Entrada Principal (CLI + Web)
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Reporta CDMX — Inspector Comunitario de Infraestructura Urbana con IA (SUAC/Locatel)"
    )
    parser.add_argument(
        "image",
        nargs="?",
        help="Ruta local a una fotografía de la falla urbana (ej. bache.jpg). Si se omite, lanza la interfaz web.",
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
        path = Path(args.image)
        if not path.exists():
            console.print(f"[red]Error: La imagen '{args.image}' no existe.[/red]")
            sys.exit(1)
        
        console.print(f"[bold green]Analizando imagen:[/bold green] {path.name}...")
        image = Image.open(path)
        report = analyze_image_with_ai(image)
        display_terminal_report(report, str(path))
        
        # Guardar copia JSON en disco
        out_file = path.with_suffix(".suac.json")
        out_file.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        console.print(f"\n[dim]Ticket SUAC guardado en: {out_file.name}[/dim]")
    else:
        # Lanzar la aplicación web interactiva
        run_web_server(port=args.port)


if __name__ == "__main__":
    main()
