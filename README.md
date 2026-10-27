# 📍 Reporta CDMX — Inspector Comunitario de Infraestructura con IA

Script autónomo bajo el estándar **PEP 723** (*Inline Script Metadata*) para la iniciativa **AI for Good en la Ciudad de México**.

Permite que cualquier habitante de la CDMX tome una fotografía desde su celular o suba una imagen de una falla en la vía pública (fuga de agua, bache, luminaria rota o tiradero de basura) y que un modelo multimodal (Gemini) la analice instantáneamente para clasificarla y generar el reporte oficial para el **SUAC (Sistema Unificado de Atención Ciudadana) / Locatel**.

---

## 🏛️ Arquitectura de Dos Portales

El servidor expone dos interfaces integradas:
1. **Portal Ciudadano (`http://localhost:8000/`):**
   * Captura fotográfica desde celular (cámara o galería) o arrastrar y soltar en desktop.
   * Diagnóstico instantáneo con IA multimodal (Gemini) mapeado al catálogo SUAC.
   * Generación de folio oficial y mensaje para WhatsApp.
2. **Mesa de Control y Despacho (`http://localhost:8000/admin`):**
   * Indicadores clave en tiempo real (Total de incidentes, Fugas críticas SACMEX, Baches, Casos atendidos).
   * Filtros por dependencia (**SACMEX**, **SOBSE**, **Alcaldías**) y por estatus.
   * Galería de evidencias fotográficas con vista ampliada (modal).
   * Cambio de estatus en 1 clic (`Pendiente` &rarr; `En Cuadrilla` &rarr; `Atendido`).
   * Exportación instantánea a **CSV** para interoperabilidad y datos abiertos (`/api/export`).

---

## 💾 Persistencia Local (SQLite)

* **Base de datos:** `reporta_cdmx.db` almacena automáticamente todos los reportes, diagnósticos y tiempos de atención.
* **Almacenamiento de imágenes:** Las fotos se guardan en `uploads/{folio}.jpg` para consulta visual por cuadrillas.
* **Auto-sembrado:** Al arrancar por primera vez, siembra automáticamente casos de prueba reales con las imágenes de `examples/`.

---

## ⚡ Ejecución Rápida (1 comando)

Gracias a **PEP 723**, no necesitas crear manualmente entornos virtuales ni instalar dependencias por separado.

### Opción A: Con `just` (Recomendado)
```bash
just run          # Iniciar el servidor web local en el puerto 8000
just test-bache   # Probar análisis con imagen de bache
just test-fuga    # Probar análisis con imagen de fuga de agua
just deploy       # Desplegar a GCP Cloud Run
```

### Opción B: Con `uv` directamente
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
