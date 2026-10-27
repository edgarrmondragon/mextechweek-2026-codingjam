# 📍 Reporta CDMX — Inspector Comunitario de Infraestructura con IA

Script autónomo bajo el estándar **PEP 723** (*Inline Script Metadata*) para la iniciativa **AI for Good en la Ciudad de México**.

Permite que cualquier habitante de la CDMX tome una fotografía desde su celular o suba una imagen de una falla en la vía pública (fuga de agua, bache, luminaria rota o tiradero de basura) y que un modelo multimodal (Gemini) la analice instantáneamente para clasificarla y generar el reporte oficial para el **SUAC (Sistema Unificado de Atención Ciudadana) / Locatel**.

---

## ⚡ Ejecución Rápida (1 comando)

Gracias a **PEP 723**, no necesitas crear manualmente entornos virtuales ni instalar dependencias por separado.

### Opción A: Con `uv` (Recomendado)
```bash
cd /Users/edgarramirez/Code/edgarrmondragon/mextechweek-2026-codingjam

# Iniciar la interfaz web con cámara y pruebas interactivas:
uv run reporta_cdmx.py

# O analizar una foto directamente desde la terminal:
uv run reporta_cdmx.py bache.jpg
```

### Opción B: Con `pipx`
```bash
pipx run reporta_cdmx.py
```

### Opción C: Con entorno virtual estándar de Python
```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install google-genai pillow pydantic rich

python reporta_cdmx.py
```

---

## 🔑 Configuración con archivo `.env`

El script carga automáticamente tus variables desde el archivo `.env` en la raíz del proyecto:
```ini
GEMINI_API_KEY="tu-clave-aqui"
# También soporta GOOGLE_API_KEY o GOOGLE_GENAI_API_KEY
```

> **Nota para la demo:** Si **no** hay clave en el `.env`, el script entra automáticamente en **Modo Demostración / Fallback**, generando reportes realistas con folios y datos oficiales del catálogo de la CDMX para que nunca falle tu presentación.

---

## 🏗️ Mapeo Oficial de Dependencias en CDMX

El modelo asigna la dependencia según la infraestructura detectada:
- **SACMEX:** Fugas de agua potable, drenaje colapsado, tomas secundarias y socavones hídricos.
- **SOBSE:** Baches, carpeta asfáltica y luminarias en vías primarias (Insurgentes, Periférico, Viaducto, Reforma).
- **Alcaldías (las 16 demarcaciones):** Calles secundarias, recolección de basura, tiraderos clandestinos y parques locales.
