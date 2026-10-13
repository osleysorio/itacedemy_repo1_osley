import itertools
import re
from collections import Counter

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from plotly.subplots import make_subplots
from pymongo import MongoClient
from scipy import stats as sps
from pymongo.errors import PyMongoError

# ==========================================================
# 1. CONFIGURACIÓN
# ==========================================================
st.set_page_config(page_title="Analytics - Sistema Médico", layout="wide", page_icon="📊")

st.markdown("""
    <style>
    .main .block-container { padding-top: 2rem; padding-bottom: 2rem; }
    h1 { font-weight: 800; color: #1E293B; letter-spacing: -0.5px; }
    h2 { font-weight: 700; color: #334155; border-bottom: 2px solid #E2E8F0; padding-bottom: 8px; }
    h3 { font-weight: 600; color: #475569; }
    .stMetric { background-color: #F8FAFC; padding: 15px; border-radius: 10px; border: 1px solid #E2E8F0; }
    </style>
""", unsafe_allow_html=True)

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "sistema_medico_procesado"
COLECCION_NAME = "reportes_raw1"

ESTUDIOS = "reporte_estructurado.estudios_detallados"
EXCLUIR_VALORES = ["NINGUNO", "NINGUNA", "VER RAÍZ", "N/A", "NO APLICA", ""]

# Palabras clave de la clasificación heurística (también editables desde el panel lateral).
# Se buscan como INICIO de palabra y sin distinguir mayúsculas ("normal" no coincide con "anormal").
RELEVANTES_DEFECTO = ["hemorrag", "fractura", "neumot", "embol", "aneurism", "infarto", "isquemi", "masa",
                      "tumor", "neoplas", "metast", "metást", "absceso", "perfora", "obstrucc", "trombo",
                      "disecci", "derrame", "consolidaci", "hematoma", "nódul", "nodul"]
NORMALES_DEFECTO = ["normal", "sin alteraciones", "sin hallazgos", "sin evidencia", "sin signos",
                    "dentro de límites", "no se observan", "no se evidencian"]
NEGACIONES = r"(?:sin|no|ni|descart\w*|ausencia de|negativ\w*)"
ORDEN_CLASIF = ["RELEVANTE", "NORMAL", "OTROS HALLAZGOS", "SIN CONCLUSIÓN"]
COLORES_CLASIF = {"RELEVANTE": "#EF4444", "NORMAL": "#10B981", "OTROS HALLAZGOS": "#F59E0B",
                  "SIN CONCLUSIÓN": "#94A3B8"}
DIAS = {1: "Lun", 2: "Mar", 3: "Mié", 4: "Jue", 5: "Vie", 6: "Sáb", 7: "Dom"}
LIMITES_LONGITUD = [0, 500, 1000, 2000, 4000, 8000, 16000, 10**9]

# Campos opcionales: déjalos en None para autodetectar, o escribe el nombre exacto (p. ej. "fecha_estudio")
CAMPO_FECHA = None
CAMPO_EDAD = None
CANDIDATOS_FECHA = ["fecha", "fecha_estudio", "fecha_informe", "fecha_realizacion", "date"]
CANDIDATOS_EDAD = ["edad", "edad_paciente", "age"]

st.title("📊 Dashboard de Analítica Radiológica")
st.markdown("Métricas demográficas, operativas y clínicas extraídas con IA, con cruces entre campos.")


# ==========================================================
# 2. CONEXIÓN
# ==========================================================
@st.cache_resource
def obtener_conexion():
    return MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)


try:
    collection = obtener_conexion()[DB_NAME][COLECCION_NAME]
    total_base = collection.count_documents({})
except PyMongoError as e:
    st.error(f"No se pudo conectar a MongoDB: {e}")
    st.stop()


# ==========================================================
# 2b. CACHÉ EN MEMORIA + DISCO
#     Los resultados se guardan en disco y solo se recalculan si cambia la base de datos o el código de este archivo.
#     Se detecta con una «huella» barata de la colección (nº de documentos, tamaño y contadores de escritura de WiredTiger).
# ==========================================================
import functools
import hashlib
import json
import os
import pickle
from pathlib import Path

CACHE_DIR = Path(os.environ.get("DASHBOARD_CACHE", ".cache_dashboard"))
try:
    CODIGO_VERSION = hashlib.md5(Path(__file__).read_bytes()).hexdigest()[:8]   # si editas el código, se invalida la caché
except Exception:
    CODIGO_VERSION = "dev"


def _leer_huella():
    try:
        s = next(iter(collection.aggregate([{"$collStats": {"storageStats": {}}}])))["storageStats"]
    except Exception:
        try:
            s = collection.database.command("collStats", collection.name)
        except Exception:
            s = {}
    cur = (s.get("wiredTiger") or {}).get("cursor") or {}
    if s.get("count") is None:
        partes = [collection.estimated_document_count()]
    else:
        partes = [s.get("count"), s.get("size"), s.get("totalIndexSize")] + [cur.get(k) for k in ("insert calls", "update calls", "modify calls", "remove calls")]
    return hashlib.md5("|".join(map(str, partes)).encode()).hexdigest()[:10]


@st.cache_data(ttl=10, show_spinner=False)
def _huella_bd():
    return _leer_huella()


@st.cache_resource
def _estado_cache():
    return {"huella": None}


def _limpiar_obsoletos(huella):
    """Borra de disco los resultados de otras huellas (base antigua) o de otra versión del código."""
    CACHE_DIR.mkdir(exist_ok=True)
    for p in CACHE_DIR.glob("*.pkl"):
        if not p.name.startswith(f"{CODIGO_VERSION}_{huella}_"):
            try:
                p.unlink()
            except OSError:
                pass


def huella_actual():
    h = _huella_bd()
    est = _estado_cache()
    if est["huella"] != h:
        _limpiar_obsoletos(h)
        est["huella"] = h
    return h


def _desde_disco(nombre, fn, huella, args, kw):
    CACHE_DIR.mkdir(exist_ok=True)
    clave = hashlib.md5(json.dumps([nombre, args, kw], default=str, sort_keys=True).encode()).hexdigest()[:16]
    ruta = CACHE_DIR / f"{CODIGO_VERSION}_{huella}_{nombre}_{clave}.pkl"
    if ruta.exists():
        try:
            with open(ruta, "rb") as fh:
                return pickle.load(fh)
        except Exception:
            pass                                    # archivo dañado: se recalcula
    valor = fn(*args, **kw)
    tmp = ruta.with_name(f"{ruta.name}.{os.getpid()}.tmp")
    try:
        with open(tmp, "wb") as fh:
            pickle.dump(valor, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, ruta)
    except Exception:
        pass                                        # si no se puede escribir en disco, sigue funcionando solo en memoria
    return valor


def cache_bd(fn=None, *, ttl=None, show_spinner=True, **_):
    """Sustituye a st.cache_data: memoria + disco, invalidada solo por cambios en la base de datos o en el código (ttl se ignora)."""
    def deco(f_):
        @st.cache_data(show_spinner=show_spinner, max_entries=2000)
        def _memo(nombre, huella, args, kw):
            return _desde_disco(nombre, f_, huella, args, kw)

        @functools.wraps(f_)
        def envoltura(*args, **kw):
            return _memo(f_.__name__, huella_actual(), args, kw)
        return envoltura
    return deco(fn) if callable(fn) else deco


def invalidar_cache():
    """Borra la caché de memoria y de disco (tras escribir en la base de datos o con el botón «Recalcular todo»)."""
    st.cache_data.clear()
    try:
        for p in CACHE_DIR.glob("*.pkl"):
            p.unlink()
    except OSError:
        pass
# ---- fin caché ----


@cache_bd(ttl=3600)
def detectar_campos():
    claves = set()
    for d in collection.find({}, {"resto_html": 0}, limit=200):
        claves.update(d.keys())
    fecha = CAMPO_FECHA or next((c for c in CANDIDATOS_FECHA if c in claves), None)
    edad = CAMPO_EDAD or next((c for c in CANDIDATOS_EDAD if c in claves), None)
    return fecha, edad


fecha_campo, edad_campo = detectar_campos()

st.sidebar.header("⚙️ Servidor MongoDB")
st.sidebar.success(f"⚡ Conectado: {total_base:,} documentos.")
st.sidebar.caption(f"Campo fecha: {fecha_campo or 'no detectado'} · Campo edad: {edad_campo or 'no detectado'}")


# ==========================================================
# 3. HELPERS DE PIPELINES
# ==========================================================
def norm(expr):
    """Mongo: convierte a string, recorta espacios y pasa a mayúsculas."""
    return {"$toUpper": {"$trim": {"input": {
        "$convert": {"input": expr, "to": "string", "onError": "", "onNull": ""}}}}}


_ACENTOS_MIN = [("á", "Á"), ("é", "É"), ("í", "Í"), ("ó", "Ó"), ("ú", "Ú"), ("ü", "Ü"), ("ñ", "Ñ")]
EXCLUIR_VALORES_N = [v.upper().strip(" .") for v in EXCLUIR_VALORES]


def norm_texto(expr):
    """Mongo: texto de hallazgos/conclusiones comparable. $toUpper solo cambia el ASCII (deja «patolóGICA» con ó minúscula),
    así que se pasan también las tildes a mayúscula y se quitan viñetas/puntuación de los extremos:
    «Sin evidencia patológica.», «- SIN EVIDENCIA PATOLÓGICA» y «sin evidencia patológica» son el mismo valor."""
    s = norm(expr)
    for a, b in _ACENTOS_MIN:
        s = {"$replaceAll": {"input": s, "find": a, "replacement": b}}
    s = {"$replaceAll": {"input": s, "find": "  ", "replacement": " "}}
    return {"$trim": {"input": s, "chars": " .;:,-–—•*·\t"}}


def tam(expr):
    """Tamaño de un array; 0 si no es array."""
    return {"$cond": [{"$isArray": expr}, {"$size": expr}, 0]}


# Modalidades: se unifican los alias y se muestra el NOMBRE COMPLETO (en mayúsculas, como el resto de etiquetas).
REGLAS_MODALIDAD = [(r"(?:MR|RM)(?:\s+R/[ID])?", "MR"),  # RM, MR R/I, MR R/D ... -> MR
                    (r"(?:CT|TC)", "CT")]                  # TC -> CT
NOMBRES_MODALIDAD = {"MR": "RESONANCIA MAGNÉTICA", "CT": "TOMOGRAFÍA COMPUTARIZADA", "US": "ECOGRAFÍA",
                     "RX": "RADIOGRAFÍA", "CR": "RADIOGRAFÍA", "DX": "RADIOGRAFÍA", "MG": "MAMOGRAFÍA",
                     "NM": "MEDICINA NUCLEAR", "PT": "PET", "XA": "ANGIOGRAFÍA", "RF": "FLUOROSCOPIA",
                     "BMD": "DENSITOMETRÍA ÓSEA", "DXA": "DENSITOMETRÍA ÓSEA", "OT": "OTROS"}


def modalidad_unificada(valor):
    v = re.sub(r"\s+", " ", str(valor).strip().upper())
    codigo = next((d for pat, d in REGLAS_MODALIDAD if re.fullmatch(pat, v)), v)
    return NOMBRES_MODALIDAD.get(codigo, codigo)


def expr_modalidad(campo="$modalidad"):
    """Mongo: modalidad normalizada, con alias unificados y nombre completo (RM→MR→RESONANCIA MAGNÉTICA)."""
    base = norm(campo)
    codigo = {"$switch": {"branches": [{"case": {"$regexMatch": {"input": base, "regex": f"^{p}$"}}, "then": d}
                                       for p, d in REGLAS_MODALIDAD], "default": base}}
    return {"$let": {"vars": {"c": codigo}, "in": {"$switch": {
        "branches": [{"case": {"$eq": ["$$c", k]}, "then": n} for k, n in NOMBRES_MODALIDAD.items()], "default": "$$c"}}}}


def filtro_modalidad(valor):
    """Filtro de Mongo para el nombre elegido (o un código) y todas sus variantes."""
    codigos = [k for k, n in NOMBRES_MODALIDAD.items() if n == valor] or [valor]
    pats = [p for p, d in REGLAS_MODALIDAD if d in codigos]
    pats += [re.escape(k) for k in codigos if not any(d == k for _, d in REGLAS_MODALIDAD)] + [re.escape(valor)]
    return {"$regex": rf"^\s*(?:{'|'.join(pats)})\s*$", "$options": "i"}


def filtro_estudio(path, estudio):
    return {path: {"$regex": rf"^\s*{re.escape(estudio)}\s*$", "$options": "i"}}


# ==========================================================
# Normalización de nombres de estudio
#   · se EXCLUYEN los bloques cuyo «estudio» es en realidad un hallazgo o una estructura anatómica
#   · se UNIFICAN las variantes del mismo estudio («RAYOS X DE TÓRAX», «RX DE TORAX», «RADIOGRAFÍA DE TÓRAX PA», «TÓRAX»…)
# Los patrones se escriben en MAYÚSCULAS y sin tildes; rx() los amplía para aceptar tildes y minúsculas (PCRE de MongoDB y re de Python).
# ==========================================================
_VOCALES = {"A": "[AÁáa]", "E": "[EÉée]", "I": "[IÍíi]", "O": "[OÓóo]", "U": "[UÚúu]"}


def rx(p):
    return re.sub(r"[AEIOU]", lambda m: _VOCALES[m.group()], p)


# Editables: nombres que NO son un estudio (hallazgos o estructuras que el modelo convirtió en bloque)
NO_ESTUDIO_RX = r"LORDOSIS|CIFOSIS|ESCOLIOSIS|ARTROSIS|FISURA|ALINEACION|ESTRUCTURAS|\bNORMAL\b|CONSERVAD|SIMETRIC|^ORBITAS?$|OCCIPITO"
NO_UNIFICAR_RX = r"\bPET\b|GAMMAGRAF|SPECT|DENSITOMETR"          # se dejan tal cual
MAMOGRAFIA_RX = r"MAMOGRAF|MAMMOGRAM"
MODALIDADES_ESTUDIO = [(r"RESONANCIA|\bRMI?\b|\bRMN\b|\bRNM\b", "RESONANCIA MAGNÉTICA"),
                       (r"TOMOGRAF|\bTAC\b|\bTC\b|\bTCMD\b", "TOMOGRAFÍA COMPUTARIZADA"),
                       (r"ECOGRAF|\bECO\b|DOPPLER|ULTRASONID", "ECOGRAFÍA"),
                       (r"RADIOGRAF|RAYOS ?X|\bRX\b|RADIOLOG", "RADIOGRAFÍA")]
REGIONES_ESTUDIO = [(r"DORSO.?LUMBAR|DORSOLUMBAR|TORACOLUMBAR", "COLUMNA DORSOLUMBAR"), (r"LUMBAR|LUMBO|SACR", "COLUMNA LUMBAR"),
                    (r"CERVICAL", "COLUMNA CERVICAL"), (r"DORSAL", "COLUMNA DORSAL"), (r"COLUMNA", "COLUMNA VERTEBRAL"),
                    (r"TORAX|TORACIC|PULMON", "TÓRAX"), (r"CRANE|CABEZA|ENCEFAL|CEREBR", "CRÁNEO"), (r"SENOS", "SENOS PARANASALES"),
                    (r"RODILLA", "RODILLA"), (r"HOMBRO", "HOMBRO"), (r"CADERA", "CADERA"), (r"TOBILLO", "TOBILLO"),
                    (r"\bPIE\b", "PIE"), (r"\bMANO\b", "MANO"), (r"MU.ECA", "MUÑECA"), (r"CODO", "CODO"),
                    (r"ABDOMEN.*PELVI|PELVI.*ABDOMEN|ABDOMINOPELVI", "ABDOMEN Y PELVIS"), (r"ABDOMEN|ABDOMINAL", "ABDOMEN"), (r"PELVIS|PELVIC", "PELVIS"),
                    (r"FEMUR", "FÉMUR"), (r"TIBIA|PERONE", "PIERNA"), (r"CLAVICULA", "CLAVÍCULA"), (r"COSTILLA|PARRILLA COSTAL", "COSTILLAS"),
                    (r"CUELLO", "CUELLO")]
SOLO_REGION_ES_RADIOGRAFIA = ["COLUMNA DORSOLUMBAR", "COLUMNA LUMBAR", "COLUMNA CERVICAL", "COLUMNA DORSAL", "COLUMNA VERTEBRAL", "TÓRAX"]


def expr_es_hallazgo(base):
    """Mongo: true si el nombre (ya normalizado) NO es un estudio."""
    return {"$regexMatch": {"input": base, "regex": rx(NO_ESTUDIO_RX), "options": "i"}}


def expr_estudio(ref):
    """Mongo: nombre de estudio unificado («<MODALIDAD> DE <REGIÓN>»); si no se reconoce, el nombre original normalizado."""
    def conmutar(reglas):
        return {"$switch": {"branches": [{"case": {"$regexMatch": {"input": "$$b", "regex": rx(p), "options": "i"}}, "then": n}
                                         for p, n in reglas], "default": None}}
    cuerpo = {"$switch": {"branches": [
        {"case": {"$regexMatch": {"input": "$$b", "regex": rx(NO_UNIFICAR_RX), "options": "i"}}, "then": "$$b"},
        {"case": {"$regexMatch": {"input": "$$b", "regex": rx(MAMOGRAFIA_RX), "options": "i"}}, "then": "MAMOGRAFÍA"},
        {"case": {"$eq": ["$$r", None]}, "then": "$$b"},
        {"case": {"$ne": ["$$m", None]}, "then": {"$concat": ["$$m", " DE ", "$$r"]}},
        {"case": {"$in": ["$$r", SOLO_REGION_ES_RADIOGRAFIA]}, "then": {"$concat": ["RADIOGRAFÍA DE ", "$$r"]}}],
        "default": "$$b"}}
    return {"$let": {"vars": {"b": norm(ref)}, "in": {"$let": {
        "vars": {"m": conmutar(MODALIDADES_ESTUDIO), "r": conmutar(REGIONES_ESTUDIO)}, "in": cuerpo}}}}


def estudios_validos():
    """Mongo: array de estudios del informe SIN los bloques que son hallazgos."""
    return {"$filter": {"input": {"$cond": [{"$isArray": f"${ESTUDIOS}"}, f"${ESTUDIOS}", []]}, "as": "e",
                        "cond": {"$not": [expr_es_hallazgo(norm("$$e.estudio"))]}}}


def estudios_unicos():
    """Mongo: nombres de estudio unificados y distintos de un informe."""
    return {"$setUnion": [{"$map": {"input": estudios_validos(), "as": "e", "in": expr_estudio("$$e.estudio")}}, []]}


def canon_estudio_py(nombre):
    """Misma lógica en Python (para el panel de verificación). Devuelve (nombre_unificado | None si no es estudio)."""
    b = re.sub(r"\s+", " ", str(nombre or "").strip().upper())
    f = lambda p: re.search(rx(p), b, re.I)
    if not b or f(NO_ESTUDIO_RX):
        return None
    if f(NO_UNIFICAR_RX):
        return b
    if f(MAMOGRAFIA_RX):
        return "MAMOGRAFÍA"
    m = next((n for p, n in MODALIDADES_ESTUDIO if f(p)), None)
    r = next((n for p, n in REGIONES_ESTUDIO if f(p)), None)
    if r is None:
        return b
    if m:
        return f"{m} DE {r}"
    return f"RADIOGRAFÍA DE {r}" if r in SOLO_REGION_ES_RADIOGRAFIA else b




def expr_fecha():
    return {"$convert": {"input": f"${fecha_campo}", "to": "date", "onError": None, "onNull": None}}


def expr_edad():
    return {"$convert": {"input": f"${edad_campo}", "to": "double", "onError": None, "onNull": None}}


GRUPO_EDAD = {"$switch": {"branches": [
    {"case": {"$eq": ["$edad", None]}, "then": "SIN DATO"},
    {"case": {"$lt": ["$edad", 0]}, "then": "SIN DATO"},
    {"case": {"$lt": ["$edad", 18]}, "then": "00-17"},
    {"case": {"$lt": ["$edad", 30]}, "then": "18-29"},
    {"case": {"$lt": ["$edad", 45]}, "then": "30-44"},
    {"case": {"$lt": ["$edad", 60]}, "then": "45-59"},
    {"case": {"$lt": ["$edad", 75]}, "then": "60-74"},
    {"case": {"$lt": ["$edad", 121]}, "then": "75+"},
], "default": "SIN DATO"}}


def _patron(kws):
    return "|".join(rf"\b{re.escape(k)}" for k in kws)


def expr_coincide(arr, kws, ignorar_negadas=False):
    """True si ALGÚN texto del array contiene alguna palabra clave (opcionalmente ignorando negaciones)."""
    if not kws:
        return {"$literal": False}
    pos = _patron(kws)
    texto = {"$convert": {"input": "$$t", "to": "string", "onError": "", "onNull": ""}}
    cond = {"$regexMatch": {"input": texto, "regex": pos, "options": "i"}}
    if ignorar_negadas:
        neg = rf"\b{NEGACIONES}\b(\W+\w+){{0,3}}?\W+(?:{pos})"
        cond = {"$and": [cond, {"$not": [{"$regexMatch": {"input": texto, "regex": neg, "options": "i"}}]}]}
    return {"$anyElementTrue": [{"$map": {"input": arr, "as": "t", "in": cond}}]}


def expr_clasificacion(f):
    """RELEVANTE / SIN CONCLUSIÓN / NORMAL / OTROS HALLAZGOS para cada estudio (heurística por palabras clave)."""
    seguro = lambda c: {"$cond": [{"$isArray": f"${c}"}, f"${c}", []]}
    todo = {"$concatArrays": [seguro("hallazgos"), seguro("conclusiones")]}
    return {"$switch": {"branches": [
        {"case": expr_coincide(todo, f["relevantes"], f["negacion"]), "then": "RELEVANTE"},
        {"case": {"$eq": [tam("$conclusiones"), 0]}, "then": "SIN CONCLUSIÓN"},
        {"case": expr_coincide(seguro("conclusiones"), f["normales"], False), "then": "NORMAL"},
    ], "default": "OTROS HALLAZGOS"}}


def match_base(f):
    """Filtro a nivel de informe: solo informes procesados con estudios + filtros del sidebar."""
    m = {ESTUDIOS: {"$type": "array"}, f"{ESTUDIOS}.0": {"$exists": True}}
    if f["genero"] != "TODOS":
        m["sexo"] = f["genero"]
    if f["modalidad"] != "TODOS":
        m["modalidad"] = filtro_modalidad(f["modalidad"])
    if f["estudio"] != "TODOS":  # informes que contienen ese estudio (por nombre unificado)
        m["$expr"] = {"$in": [f["estudio"], estudios_unicos()]}
    return m


def etapas_estudio(f, clasif=False):
    """Un documento por estudio con: sexo, modalidad, estudio, hallazgos[], conclusiones[] (+edad, mes, clasificacion)."""
    etapas = [{"$match": match_base(f)}, {"$unwind": f"${ESTUDIOS}"},
              {"$match": {"$expr": {"$not": [expr_es_hallazgo(norm(f"${ESTUDIOS}.estudio"))]}}}]  # fuera los «estudios» que son hallazgos
    proy = {
        "_id": 0, "sexo": 1, "modalidad": expr_modalidad(),
        "estudio": expr_estudio(f"${ESTUDIOS}.estudio"),  # nombre unificado
        "hallazgos": f"${ESTUDIOS}.hallazgos",
        "conclusiones": f"${ESTUDIOS}.conclusiones",
    }
    if edad_campo:
        proy["edad"] = expr_edad()
    if fecha_campo:
        proy["mes"] = {"$dateToString": {"format": "%Y-%m", "date": expr_fecha(), "onNull": "SIN FECHA"}}
    etapas.append({"$project": proy})
    if f["estudio"] != "TODOS":  # solo el estudio elegido (no los otros del mismo informe)
        etapas.append({"$match": {"estudio": f["estudio"]}})
    if clasif:
        etapas.append({"$addFields": {"clasificacion": expr_clasificacion(f)}})
    return etapas


def ejecutar(pipeline, columnas):
    df = pd.DataFrame(list(collection.aggregate(pipeline, allowDiskUse=True)))
    if not df.empty:
        df.columns = columnas
    return df


def div(a, b):
    return (a / b) if b else 0.0


# ==========================================================
# 4. CONSULTAS: SECCIÓN GLOBAL
# ==========================================================
def _cuenta_si(cond):
    return {"$sum": {"$cond": [cond, 1, 0]}}


def _vacio(campo):
    return {"$in": [{"$ifNull": [f"${campo}", ""]}, [""]]}


@cache_bd(ttl=120)
def obtener_resumen_global():
    es_true = lambda c: {"$eq": [{"$ifNull": [f"${c}", False]}, True]}
    pipeline = [{"$group": {
        "_id": None,
        "total": {"$sum": 1},
        "ok": _cuenta_si({"$and": [es_true("procesado_por_ia"), {"$not": [es_true("tiene_problema_estructural")]}]}),
        "problema": _cuenta_si(es_true("tiene_problema_estructural")),
        "sin_sexo": _cuenta_si(_vacio("sexo")),
        "sin_modalidad": _cuenta_si(_vacio("modalidad")),
    }}]
    res = list(collection.aggregate(pipeline, allowDiskUse=True))
    r = res[0] if res else {"total": 0, "ok": 0, "problema": 0, "sin_sexo": 0, "sin_modalidad": 0}
    r["pendientes"] = max(r["total"] - r["ok"] - r["problema"], 0)
    return r


@cache_bd(ttl=120)
def obtener_distribucion(campo, etiqueta):
    df = ejecutar([{"$group": {"_id": expr_modalidad() if campo == "modalidad" else norm(f"${campo}"), "n": {"$sum": 1}}}], [etiqueta, "Cantidad"])
    if not df.empty:
        df[etiqueta] = df[etiqueta].replace("", "NO ESPECIFICADO")
        df = df.groupby(etiqueta, as_index=False)["Cantidad"].sum()
    return df


@cache_bd(ttl=120)
def obtener_status_x_modalidad():
    pipeline = [{"$group": {"_id": {"s": norm("$status"), "m": expr_modalidad()}, "n": {"$sum": 1}}}]
    filas = [{"Status": d["_id"]["s"] or "NO ESPECIFICADO", "Modalidad": d["_id"]["m"] or "NO ESPECIFICADO",
              "Cantidad": d["n"]} for d in collection.aggregate(pipeline, allowDiskUse=True)]
    return pd.DataFrame(filas)


@cache_bd(ttl=120)
def obtener_motivos_fallo():
    pipeline = [{"$match": {"tiene_problema_estructural": True}},
                {"$group": {"_id": "$motivo_fallo", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 50}]
    df = ejecutar(pipeline, ["Motivo", "Cantidad"])
    if not df.empty:
        df["Motivo"] = df["Motivo"].fillna("Sin motivo").astype(str).str.replace(r"\d+", "N", regex=True)
        df = df.groupby("Motivo", as_index=False)["Cantidad"].sum().sort_values("Cantidad", ascending=False)
    return df


@cache_bd(ttl=300)
def obtener_volumen_mensual():
    pipeline = [{"$project": {"mes": {"$dateToString": {"format": "%Y-%m", "date": expr_fecha(), "onNull": None}}}},
                {"$match": {"mes": {"$ne": None}}},
                {"$group": {"_id": "$mes", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    return ejecutar(pipeline, ["Mes", "Informes"])


@cache_bd(ttl=300)
def obtener_edad_global():
    limites = [0, 18, 30, 45, 60, 75, 90, 130]
    etiquetas = {0: "00-17", 18: "18-29", 30: "30-44", 45: "45-59", 60: "60-74", 75: "75-89", 90: "90+"}
    pipeline = [{"$project": {"e": expr_edad()}},
                {"$bucket": {"groupBy": "$e", "boundaries": limites, "default": "SIN DATO",
                             "output": {"n": {"$sum": 1}}}}]
    df = ejecutar(pipeline, ["Grupo", "Cantidad"])
    if not df.empty:
        df["Grupo"] = df["Grupo"].map(lambda x: etiquetas.get(x, x))
    media = list(collection.aggregate([
        {"$project": {"e": expr_edad()}}, {"$match": {"e": {"$gte": 0, "$lte": 120}}},
        {"$group": {"_id": None, "media": {"$avg": "$e"}}}]))
    return df, (media[0]["media"] if media else None)


def _es_true(c):
    return {"$eq": [{"$ifNull": [f"${c}", False]}, True]}


@cache_bd(ttl=300)
def obtener_calidad_por(campo):
    """Estado del procesamiento IA (OK / problema / pendiente) por segmento."""
    pipeline = [{"$group": {
        "_id": expr_modalidad() if campo == "modalidad" else norm(f"${campo}"), "total": {"$sum": 1},
        "problema": _cuenta_si(_es_true("tiene_problema_estructural")),
        "ok": _cuenta_si({"$and": [_es_true("procesado_por_ia"), {"$not": [_es_true("tiene_problema_estructural")]}]}),
    }}, {"$sort": {"total": -1}}, {"$limit": 30}]
    df = ejecutar(pipeline, ["Segmento", "Total", "Con problema", "Procesados OK"])
    if not df.empty:
        df["Segmento"] = df["Segmento"].replace("", "NO ESPECIFICADO")
        df["Pendientes"] = (df["Total"] - df["Con problema"] - df["Procesados OK"]).clip(lower=0)
        procesados = df["Con problema"] + df["Procesados OK"]
        df["% problema"] = df["Con problema"] / procesados.where(procesados > 0) * 100
    return df


@cache_bd(ttl=600, show_spinner="Analizando longitud de textos (muestra)...")
def obtener_longitud_vs_fallo():
    """Tasa de problemas de la IA según el tamaño del HTML original (muestra aleatoria de 50.000 docs)."""
    pipeline = [
        {"$sample": {"size": 50000}},
        {"$project": {"largo": {"$strLenCP": {"$convert": {"input": "$resto_html", "to": "string",
                                                            "onError": "", "onNull": ""}}},
                      "prob": {"$cond": [_es_true("tiene_problema_estructural"), 1, 0]},
                      "proc": {"$cond": [_es_true("procesado_por_ia"), 1, 0]}}},
        {"$match": {"proc": 1}},
        {"$bucket": {"groupBy": "$largo", "boundaries": LIMITES_LONGITUD, "default": "OTRO",
                     "output": {"n": {"$sum": 1}, "problemas": {"$sum": "$prob"}}}},
    ]
    df = ejecutar(pipeline, ["Desde", "Informes", "Con problema"])
    if df.empty:
        return df
    etiquetas = {0: "<500", 500: "500-1k", 1000: "1k-2k", 2000: "2k-4k", 4000: "4k-8k", 8000: "8k-16k", 16000: ">16k"}
    df["Rango (caracteres HTML)"] = df["Desde"].map(lambda x: etiquetas.get(x, "OTRO"))
    df = df[df["Rango (caracteres HTML)"].isin(etiquetas.values())].copy()
    df["Rango (caracteres HTML)"] = pd.Categorical(df["Rango (caracteres HTML)"], list(etiquetas.values()), ordered=True)
    df["% problema"] = df["Con problema"] / df["Informes"] * 100
    return df.sort_values("Rango (caracteres HTML)")


@cache_bd(ttl=300)
def obtener_completitud(campos):
    grupo = {"_id": None, "total": {"$sum": 1}}
    for i, c in enumerate(campos):
        grupo[f"c{i}"] = _cuenta_si({"$not": [_vacio(c)]})
    res = list(collection.aggregate([{"$group": grupo}], allowDiskUse=True))
    if not res:
        return pd.DataFrame()
    r = res[0]
    return pd.DataFrame([{"Campo": c, "% completo": div(r[f"c{i}"], r["total"]) * 100}
                         for i, c in enumerate(campos)])


@cache_bd(ttl=300)
def obtener_actividad_semanal():
    pipeline = [{"$project": {"d": expr_fecha()}}, {"$match": {"d": {"$ne": None}}},
                {"$group": {"_id": {"dia": {"$isoDayOfWeek": "$d"}, "hora": {"$hour": "$d"}}, "n": {"$sum": 1}}}]
    filas = [{"dia": x["_id"]["dia"], "hora": x["_id"]["hora"], "Informes": x["n"]}
             for x in collection.aggregate(pipeline, allowDiskUse=True)]
    return pd.DataFrame(filas)


@cache_bd(ttl=300)
def obtener_piramide():
    pipeline = [{"$project": {"sexo": norm("$sexo"), "edad": expr_edad()}},
                {"$project": {"sexo": 1, "grupo": GRUPO_EDAD}},
                {"$match": {"grupo": {"$ne": "SIN DATO"}, "sexo": {"$nin": ["", None]}}},
                {"$group": {"_id": {"g": "$grupo", "s": "$sexo"}, "n": {"$sum": 1}}}]
    filas = [{"Grupo": x["_id"]["g"], "Sexo": x["_id"]["s"], "Cantidad": x["n"]}
             for x in collection.aggregate(pipeline, allowDiskUse=True)]
    return pd.DataFrame(filas)


@cache_bd(ttl=300)
def obtener_edad_por_modalidad():
    df = ejecutar([{"$project": {"m": expr_modalidad(), "e": expr_edad()}},
                   {"$match": {"e": {"$gte": 0, "$lte": 120}}},
                   {"$group": {"_id": "$m", "media": {"$avg": "$e"}, "n": {"$sum": 1}}},
                   {"$sort": {"n": -1}}, {"$limit": 15}], ["Modalidad", "Edad media", "Informes"])
    if not df.empty:
        df["Modalidad"] = df["Modalidad"].replace("", "NO ESPECIFICADO")
    return df


# ==========================================================
# 5. CONSULTAS: SECCIONES CLÍNICAS (dependen de los filtros f)
# ==========================================================
@cache_bd(ttl=600)
def obtener_opciones_filtros():
    generos = sorted(g for g in collection.distinct("sexo") if g and not pd.isna(g))
    modalidades = sorted({modalidad_unificada(m) for m in collection.distinct("modalidad") if m and not pd.isna(m)} - {""})
    pipeline = [{"$unwind": f"${ESTUDIOS}"},
                {"$match": {"$expr": {"$not": [expr_es_hallazgo(norm(f"${ESTUDIOS}.estudio"))]}}},
                {"$project": {"_id": 0, "estudio": expr_estudio(f"${ESTUDIOS}.estudio")}},
                {"$group": {"_id": "$estudio"}}, {"$match": {"_id": {"$nin": [None, ""]}}}]
    estudios = sorted(d["_id"] for d in collection.aggregate(pipeline, allowDiskUse=True))
    return ["TODOS"] + generos, ["TODOS"] + modalidades, ["TODOS"] + estudios


@cache_bd(ttl=300)
def obtener_kpis_clinicos(f):
    est = estudios_validos()
    suma = lambda campo: {"$sum": {"$map": {"input": est, "as": "e", "in": tam(f"$$e.{campo}")}}}
    sin = lambda campo: tam({"$filter": {"input": est, "as": "e",
                                         "cond": {"$eq": [tam(f"$$e.{campo}"), 0]}}})
    pipeline = [
        {"$match": match_base(f)},
        {"$project": {"n_est": tam(est), "n_uni": {"$size": estudios_unicos()}, "n_h": suma("hallazgos"), "n_c": suma("conclusiones"),
                      "sin_h": sin("hallazgos"), "sin_c": sin("conclusiones")}},
        {"$group": {"_id": None, "informes": {"$sum": 1}, "estudios": {"$sum": "$n_est"},
                    "hallazgos": {"$sum": "$n_h"}, "conclusiones": {"$sum": "$n_c"},
                    "multi": _cuenta_si({"$gt": ["$n_uni", 1]}),
                    "est_sin_h": {"$sum": "$sin_h"}, "est_sin_c": {"$sum": "$sin_c"}}},
    ]
    res = list(collection.aggregate(pipeline, allowDiskUse=True))
    return res[0] if res else {"informes": 0, "estudios": 0, "hallazgos": 0, "conclusiones": 0,
                               "multi": 0, "est_sin_h": 0, "est_sin_c": 0}


@cache_bd(ttl=300)
def obtener_demografia(f):
    g = {**f, "genero": "TODOS"}  # el gráfico de género ignora el filtro de género
    df = ejecutar([{"$match": match_base(g)}, {"$group": {"_id": norm("$sexo"), "n": {"$sum": 1}}}],
                  ["Sexo", "Cantidad"])
    if not df.empty:
        df["Sexo"] = df["Sexo"].replace("", "NO ESPECIFICADO")
    return df


@cache_bd(ttl=300)
def obtener_top_estudios(f):
    return ejecutar(etapas_estudio(f) + [
        {"$match": {"estudio": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$estudio", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 10}],
        ["Estudio", "Cantidad"])


@cache_bd(ttl=300)
def obtener_top_valores(f, campo):
    return ejecutar(etapas_estudio(f) + [
        {"$unwind": f"${campo}"},
        {"$project": {"v": norm_texto(f"${campo}")}},
        {"$match": {"v": {"$nin": EXCLUIR_VALORES_N}}},
        {"$group": {"_id": "$v", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 10}],
        [campo.capitalize(), "Frecuencia"])


@cache_bd(ttl=600, show_spinner="Leyendo los nombres de estudio originales...")
def obtener_nombres_estudio_crudos(f, limite=5000):
    pipeline = [{"$match": match_base({**f, "estudio": "TODOS"})}, {"$unwind": f"${ESTUDIOS}"},
                {"$project": {"_id": 0, "n": norm(f"${ESTUDIOS}.estudio")}},
                {"$group": {"_id": "$n", "c": {"$sum": 1}}}, {"$sort": {"c": -1}}, {"$limit": limite}]
    return pd.DataFrame([{"Nombre": d["_id"], "Cantidad": d["c"]} for d in collection.aggregate(pipeline, allowDiskUse=True) if d["_id"]],
                        columns=["Nombre", "Cantidad"])


@cache_bd(ttl=300)
def obtener_hist_estudios_por_informe(f, tope=10):
    pipeline = [{"$match": match_base(f)}, {"$project": {"n": {"$min": [{"$size": estudios_unicos()}, tope]}}},
                {"$match": {"n": {"$gte": 1}}},
                {"$group": {"_id": "$n", "c": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    df = ejecutar(pipeline, ["N", "Informes"])
    if not df.empty:
        df["N"] = df["N"].astype(int).astype(str)
        df.loc[df["N"] == str(tope), "N"] = f"{tope}+"
    return df


@cache_bd(ttl=300)
def obtener_hist_items_por_estudio(f, campo, tope=15):
    pipeline = etapas_estudio(f) + [{"$project": {"n": {"$min": [tam(f"${campo}"), tope]}}},
                                    {"$group": {"_id": "$n", "c": {"$sum": 1}}}, {"$sort": {"_id": 1}}]
    df = ejecutar(pipeline, ["N", "Estudios"])
    if not df.empty:
        df["N"] = df["N"].astype(int).astype(str)
        df.loc[df["N"] == str(tope), "N"] = f"{tope}+"
    return df


@cache_bd(ttl=300)
def obtener_distribucion_estudios(f):
    return ejecutar(etapas_estudio(f) + [
        {"$match": {"estudio": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$estudio", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 300}],
        ["Estudio", "Cantidad"])


@cache_bd(ttl=300)
def obtener_edad_media_por(f, dim, min_n=30):
    etapas = etapas_estudio(f)
    if dim == "Hallazgo":
        etapas.append({"$unwind": "$hallazgos"})
    etapas += [{"$project": {"v": norm("$hallazgos" if dim == "Hallazgo" else "$estudio"), "edad": 1}},
               {"$match": {"v": {"$nin": EXCLUIR_VALORES}, "edad": {"$gte": 0, "$lte": 120}}},
               {"$group": {"_id": "$v", "m": {"$avg": "$edad"}, "n": {"$sum": 1}}},
               {"$match": {"n": {"$gte": min_n}}}, {"$sort": {"n": -1}}, {"$limit": 15}]
    return ejecutar(etapas, [dim, "Edad media", "Informes"])


@cache_bd(ttl=300, show_spinner="Clasificando estudios...")
def obtener_clasificacion_total(f):
    return ejecutar(etapas_estudio(f, clasif=True) + [{"$group": {"_id": "$clasificacion", "n": {"$sum": 1}}}],
                    ["Clasificación", "Cantidad"])


# ---- Cruces configurables ----
EJES = {
    "Estudio": {"expr": "$estudio"},
    "Sexo": {"expr": "$sexo"},
    "Modalidad": {"expr": "$modalidad"},
    "Hallazgo": {"expr": "$hallazgos", "unwind": "$hallazgos", "texto": True},
    "Conclusión": {"expr": "$conclusiones", "unwind": "$conclusiones", "texto": True},
}
if edad_campo:
    EJES["Grupo de edad"] = {"expr": GRUPO_EDAD}
if fecha_campo:
    EJES["Mes"] = {"expr": "$mes"}
EJES["Clasificación"] = {"expr": "$clasificacion"}
EJES_ORDEN_NATURAL = {"Mes", "Grupo de edad"}


@cache_bd(ttl=300, show_spinner="Calculando cruce...")
def obtener_cruce(f, eje_a, eje_b):
    a, b = EJES[eje_a], EJES[eje_b]
    etapas = etapas_estudio(f, clasif="Clasificación" in (eje_a, eje_b))
    for eje in (a, b):
        if "unwind" in eje:
            etapas.append({"$unwind": eje["unwind"]})
    etapas.append({"$project": {"fila": (norm_texto if a.get("texto") else norm)(a["expr"]),
                                "col": (norm_texto if b.get("texto") else norm)(b["expr"])}})
    filtro_txt = {k: {"$nin": EXCLUIR_VALORES_N} for k, e in (("fila", a), ("col", b)) if e.get("texto")}
    if filtro_txt:
        etapas.append({"$match": filtro_txt})
    etapas += [{"$group": {"_id": {"fila": "$fila", "col": "$col"}, "n": {"$sum": 1}}},
               {"$sort": {"n": -1}}, {"$limit": 5000}]
    filas = [{"fila": d["_id"]["fila"] or "NO ESPECIFICADO", "col": d["_id"]["col"] or "NO ESPECIFICADO",
              "Cantidad": d["n"]} for d in collection.aggregate(etapas, allowDiskUse=True)]
    return pd.DataFrame(filas)


def construir_pivot(df, eje_a, eje_b, top_filas, top_cols):
    filas = df.groupby("fila")["Cantidad"].sum().nlargest(top_filas).index
    cols = df.groupby("col")["Cantidad"].sum().nlargest(top_cols).index
    sub = df[df["fila"].isin(filas) & df["col"].isin(cols)]
    if sub.empty:
        return pd.DataFrame()
    p = sub.pivot_table(index="fila", columns="col", values="Cantidad", aggfunc="sum", fill_value=0)
    p = p.reindex(index=[x for x in filas if x in p.index], columns=[x for x in cols if x in p.columns])
    if eje_a in EJES_ORDEN_NATURAL:
        p = p.sort_index()
    if eje_b in EJES_ORDEN_NATURAL:
        p = p.sort_index(axis=1)
    p.index.name, p.columns.name = eje_a, eje_b
    return p


def mostrar_heatmap(p, eje_a, eje_b, porcentaje):
    if p.empty:
        st.info("Sin datos para este cruce con los filtros actuales.")
        return
    datos = p.div(p.sum(axis=1).replace(0, 1), axis=0) * 100 if porcentaje else p
    fig = px.imshow(datos, text_auto=".0f" if porcentaje else True, aspect="auto",
                    color_continuous_scale="Blues",
                    labels=dict(x=eje_b, y=eje_a, color="% fila" if porcentaje else "Cantidad"))
    fig.update_layout(height=max(350, 34 * len(datos) + 120), margin=dict(t=20, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)


def mostrar_barras_apiladas(p, eje_a, eje_b):
    """Barras horizontales apiladas al 100 % (composición de cada fila)."""
    p = p.copy()
    p.index.name, p.columns.name = eje_a, eje_b
    pct = p.div(p.sum(axis=1).replace(0, 1), axis=0) * 100
    largo = pct.reset_index().melt(id_vars=eje_a, var_name=eje_b, value_name="%")
    fig = px.bar(largo, x="%", y=eje_a, color=eje_b, orientation="h", barmode="stack", text_auto=".0f",
                 color_discrete_map=COLORES_CLASIF if eje_b == "Clasificación" else None)
    fig.update_layout(height=max(320, 30 * len(pct) + 120), xaxis_title="% de la fila", yaxis_title=None,
                      legend_title=None, margin=dict(t=10, b=10, l=10, r=10))
    fig.update_yaxes(autorange="reversed")
    st.plotly_chart(fig, use_container_width=True)


@cache_bd(ttl=300, show_spinner="Calculando co-ocurrencias...")
def obtener_coocurrencia(f, muestra=100000):
    pipeline = [
        {"$match": match_base(f)},
        {"$project": {"_id": 0, "est": {"$setUnion": [
            {"$map": {"input": estudios_validos(), "as": "e", "in": expr_estudio("$$e.estudio")}}, []]}}},
        {"$match": {"$expr": {"$gte": [{"$size": "$est"}, 2]}}},
        {"$limit": muestra},
    ]
    pares, totales, n_informes = Counter(), Counter(), 0
    for d in collection.aggregate(pipeline, allowDiskUse=True):
        est = sorted(e for e in d["est"] if e)
        if len(est) < 2:
            continue
        n_informes += 1
        totales.update(est)
        pares.update(itertools.combinations(est, 2))
    return dict(pares), dict(totales), n_informes


# ==========================================================
# 5b. INFERENCIA ESTADÍSTICA
# ==========================================================
def wilson(k, n, alfa=0.05):
    """Intervalo de confianza de Wilson para una proporción."""
    if n == 0:
        return np.nan, np.nan
    z, p = sps.norm.ppf(1 - alfa / 2), k / n
    den = 1 + z**2 / n
    c = (p + z**2 / (2 * n)) / den
    h = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / den
    return c - h, c + h


def holm(p):
    """Corrección de Holm para comparaciones múltiples."""
    p = np.asarray(p, float)
    o, m = np.argsort(p), len(p)
    aj = np.minimum(1, np.maximum.accumulate((m - np.arange(m)) * p[o]))
    out = np.empty(m)
    out[o] = aj
    return out


def fmt_p(p):
    return "<0.0001" if p < 1e-4 else f"{p:.4f}"


def chi2_asociacion(tabla):
    """χ² de independencia + V de Cramér + residuos ajustados."""
    t = tabla.loc[tabla.sum(axis=1) > 0, tabla.sum(axis=0) > 0]
    if t.shape[0] < 2 or t.shape[1] < 2:
        return None
    chi2, p, gl, esp = sps.chi2_contingency(t.values, correction=False)
    n = t.values.sum()
    v = np.sqrt(chi2 / (n * (min(t.shape) - 1)))
    fp = t.sum(axis=1).values[:, None] / n
    cp = t.sum(axis=0).values[None, :] / n
    res = pd.DataFrame((t.values - esp) / np.sqrt(esp * (1 - fp) * (1 - cp)), index=t.index, columns=t.columns)
    return {"chi2": chi2, "p": p, "gl": gl, "n": n, "v": v, "res": res, "pct_esp_bajo": (esp < 5).mean()}


def prop_vs_resto(k, n, K, N):
    """Segmento vs resto: p-valor χ² 2x2 y odds ratio con IC 95 %."""
    a, b, c, d = k, n - k, K - k, (N - K) - (n - k)
    if n == 0 or n == N or K == 0 or K == N:
        return np.nan, np.nan, np.nan, np.nan
    p = sps.chi2_contingency([[a, b], [c, d]], correction=False)[1]
    h = 0.5 if 0 in (a, b, c, d) else 0
    lor = np.log((a + h) * (d + h) / ((b + h) * (c + h)))
    se = np.sqrt(sum(1 / (x + h) for x in (a, b, c, d)))
    return p, np.exp(lor), np.exp(lor - 1.96 * se), np.exp(lor + 1.96 * se)


def anova_desde_resumen(d):
    """ANOVA clásico y de Welch a partir de n, media y desviación por grupo."""
    d = d.dropna(subset=["n", "m", "sd"])
    d = d[d["sd"] > 0]
    k = len(d)
    if k < 2:
        return None
    n, m, v = d["n"].values.astype(float), d["m"].values, d["sd"].values ** 2
    N = n.sum()
    M = (n * m).sum() / N
    ssb, ssw = (n * (m - M) ** 2).sum(), ((n - 1) * v).sum()
    F = (ssb / (k - 1)) / (ssw / (N - k))
    w = n / v
    W = w.sum()
    mw = (w * m).sum() / W
    lam = ((1 - w / W) ** 2 / (n - 1)).sum()
    Fw = ((w * (m - mw) ** 2).sum() / (k - 1)) / (1 + 2 * (k - 2) / (k**2 - 1) * lam)
    df2w = (k**2 - 1) / (3 * lam)
    return {"k": k, "N": int(N), "F": F, "p": sps.f.sf(F, k - 1, N - k), "Fw": Fw, "df2w": df2w,
            "pw": sps.f.sf(Fw, k - 1, df2w), "eta2": ssb / (ssb + ssw)}


def tendencia_ca(r, n):
    """Test de tendencia de Cochran-Armitage + pendiente ponderada (pp por periodo)."""
    r, n = np.asarray(r, float), np.asarray(n, float)
    t, N = np.arange(len(n)), n.sum()
    p = r.sum() / N
    var = p * (1 - p) * ((n * t**2).sum() - ((n * t).sum()) ** 2 / N)
    z = (t * (r - n * p)).sum() / np.sqrt(var)
    pend = np.polyfit(t, r / n, 1, w=np.sqrt(n))[0] * 100
    return z, 2 * sps.norm.sf(abs(z)), pend


def colapsar_raras(p, top, min_total):
    """Agrupa en 'OTROS' las filas fuera del top o con pocos casos (evita celdas con E<5 sin perder N)."""
    tot = p.sum(axis=1).sort_values(ascending=False)
    keep = [x for x in tot.index[:top] if tot[x] >= min_total]
    resto = p.index.difference(keep)
    if len(resto) == 0:
        return p
    out = p.loc[[x for x in tot.index if x in keep]].copy()
    out.loc["OTROS"] = p.loc[resto].sum()
    return out


@cache_bd(ttl=300, show_spinner="Calculando tabla de contingencia (todos los registros)...")
def tabla_contingencia(f, eje_a, eje_b, top_a=40, top_b=15, min_total=50):
    """Tabla completa (sin el límite de 5.000 combinaciones); categorías raras → 'OTROS'. El N total se conserva."""
    a, b = EJES[eje_a], EJES[eje_b]
    etapas = etapas_estudio(f, clasif="Clasificación" in (eje_a, eje_b))
    for eje in (a, b):
        if "unwind" in eje:
            etapas.append({"$unwind": eje["unwind"]})
    etapas += [{"$project": {"fila": (norm_texto if a.get("texto") else norm)(a["expr"]),
                             "col": (norm_texto if b.get("texto") else norm)(b["expr"])}},
               {"$group": {"_id": {"fila": "$fila", "col": "$col"}, "n": {"$sum": 1}}}]
    filas = [{"fila": d["_id"]["fila"] or "NO ESPECIFICADO", "col": d["_id"]["col"] or "NO ESPECIFICADO",
              "Cantidad": d["n"]} for d in collection.aggregate(etapas, allowDiskUse=True)]
    if not filas:
        return pd.DataFrame()
    p = pd.DataFrame(filas).pivot_table(index="fila", columns="col", values="Cantidad", aggfunc="sum", fill_value=0)
    if eje_a not in EJES_ORDEN_NATURAL:
        p = colapsar_raras(p, top_a, min_total)
    if eje_b not in EJES_ORDEN_NATURAL:
        p = colapsar_raras(p.T, top_b, min_total).T
    p = p.sort_index() if eje_a in EJES_ORDEN_NATURAL else p
    p = p.sort_index(axis=1) if eje_b in EJES_ORDEN_NATURAL else p
    p.index.name, p.columns.name = eje_a, eje_b
    return p


@cache_bd(ttl=300, show_spinner="Calculando resúmenes de edad...")
def obtener_edad_resumen(f, dim, min_n=30):
    etapas = etapas_estudio(f, clasif=(dim == "Clasificación")) + [
        {"$project": {"g": norm(EJES[dim]["expr"]), "edad": 1}},
        {"$match": {"edad": {"$gte": 0, "$lte": 120}}},
        {"$group": {"_id": "$g", "n": {"$sum": 1}, "m": {"$avg": "$edad"}, "sd": {"$stdDevSamp": "$edad"}}},
        {"$match": {"n": {"$gte": min_n}}}, {"$sort": {"n": -1}}, {"$limit": 30}]
    df = ejecutar(etapas, ["Grupo", "n", "m", "sd"])
    if not df.empty:
        df["Grupo"] = df["Grupo"].replace("", "NO ESPECIFICADO")
    return df


def magnitud_v(v):
    return "despreciable" if v < 0.1 else "pequeña" if v < 0.3 else "moderada" if v < 0.5 else "grande"


# ==========================================================
# 5c. CONSULTAS: PROCESAMIENTO, HTML VACÍO Y CATÁLOGO DE HALLAZGOS
# ==========================================================
CAMPO_HTML = "resto_html"
@cache_bd(ttl=120)
def obtener_versiones():
    r = collection.aggregate([{"$group": {"_id": {"v": "$version_procesamiento", "m": "$modelo_procesamiento"},
                                          "n": {"$sum": 1}}}, {"$sort": {"n": -1}}])
    return pd.DataFrame([{"Versión": d["_id"].get("v", "sin versión"), "Modelo": d["_id"].get("m") or "N/D",
                          "Documentos": d["n"]} for d in r])


@cache_bd(ttl=300, show_spinner="Listando hallazgos frecuentes...")
def obtener_catalogo_hallazgos(f, top=150):
    return ejecutar(etapas_estudio(f) + [
        {"$unwind": "$hallazgos"}, {"$project": {"v": norm_texto("$hallazgos")}},
        {"$match": {"v": {"$nin": EXCLUIR_VALORES_N}}},
        {"$group": {"_id": "$v", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": top}],
        ["Hallazgo", "Frecuencia"])


@cache_bd(ttl=300, show_spinner="Calculando el aporte de cada término...")
def obtener_aporte_terminos(f, terminos):
    seguro = lambda c: {"$cond": [{"$isArray": f"${c}"}, f"${c}", []]}
    todo = {"$concatArrays": [seguro("hallazgos"), seguro("conclusiones")]}
    grupo = {"_id": None, "n": {"$sum": 1}}
    for i, k in enumerate(terminos):
        grupo[f"t{i}"] = _cuenta_si(expr_coincide(todo, (k,), f["negacion"]))
    res = list(collection.aggregate(etapas_estudio(f) + [{"$group": grupo}], allowDiskUse=True))
    if not res:
        return pd.DataFrame()
    r = res[0]
    return pd.DataFrame({"Término": list(terminos), "Estudios": [r[f"t{i}"] for i in range(len(terminos))],
                         "% de estudios": [div(r[f"t{i}"], r["n"]) * 100 for i in range(len(terminos))]})


# ==========================================================
# 5d. PARÁMETROS DEL PROCESAMIENTO (reflejan el script de extracción con Ollama)
# ==========================================================
PROC = {"modelo": "llama3.2:3b", "format": "json", "temperature": 0.0, "num_predict": 512, "num_ctx": 1024,
        "num_gpu": 99, "low_vram": False, "keep_alive": "60m", "chars_por_token": 3.5,
        "hilos": 4, "bloque": 500, "reintentos": 3, "timeout": 180, "version": 2}

PROMPT_PROC = """Eres un extractor de datos de informes radiológicos en español.
Convierte el texto del informe en JSON siguiendo el esquema indicado.

REGLAS:
1. Crea un bloque independiente por cada estudio o región explorada que aparezca en el texto.
2. "estudio": nombre del estudio tal como aparece en el texto, en mayúsculas.
3. "hallazgos": observaciones descriptivas, una por elemento, copiadas o resumidas fielmente.
4. "conclusiones": impresión diagnóstica o conclusiones del informe.
5. Si una sección no existe en el texto, devuelve una lista vacía []. No escribas "ninguno" ni "ver raíz".
6. No inventes información que no esté en el texto."""

ESQUEMA_PROC = """{ "estudios_detallados": [ { "estudio": "str", "hallazgos": ["str", ...], "conclusiones": ["str", ...] } ] }"""

# Capacidad real con estos parámetros (prompt ≈ caracteres / 3,5 + ~30 tokens de plantilla de chat)
PROMPT_TOKENS = int(len(PROMPT_PROC) / PROC["chars_por_token"]) + 30
TOKENS_INFORME = max(PROC["num_ctx"] - PROC["num_predict"] - PROMPT_TOKENS, 0)
CHARS_INFORME = int(TOKENS_INFORME * PROC["chars_por_token"])
CHARS_SALIDA = int(PROC["num_predict"] * PROC["chars_por_token"])
KV_MB = PROC["num_ctx"] * 112 / 1024  # ~112 KB por token de contexto (arquitectura de Llama 3.2 3B)

PARAMS_DOC = [
    ("Modelo", "llama3.2:3b", "LLM de Meta de ~3.200 M de parámetros (~2 GB cuantizado a 4 bits), multilingüe (incluye español).",
     "Rápido y ligero; es el punto débil para separar varios estudios y no inventar. Un modelo mayor (8B) mejora la fidelidad a costa de ~2× tiempo y memoria."),
    ("format", "'json'", "Modo JSON de Ollama: obliga a que la salida sea un JSON sintácticamente válido.",
     "NO impone qué claves lleva: la estructura (estudios_detallados → estudio / hallazgos / conclusiones) depende solo del prompt y el código debe validarla. Pasar un esquema JSON en `format` sí garantizaría la forma."),
    ("temperature", "0,0", "Aleatoriedad de la generación; 0 = siempre el token más probable.",
     "Resultados reproducibles, adecuado para extraer datos. Riesgo: en modelos pequeños puede entrar en bucles repetitivos hasta agotar num_predict."),
    ("num_predict", "512", "Máximo de tokens que puede generar el modelo.",
     f"Equivale a ~{CHARS_SALIDA:,} caracteres de JSON".replace(",", ".") + ". Un informe con varios estudios puede necesitar más: la respuesta se corta, el JSON queda inválido y el documento se marca como problema estructural."),
    ("num_ctx", "1024", "Ventana de contexto TOTAL (prompt + informe + respuesta), en tokens.",
     f"Es muy pequeña: tras el prompt (~{PROMPT_TOKENS} tokens) y los 512 de respuesta quedan ~{TOKENS_INFORME} tokens (~{CHARS_INFORME:,} caracteres) para el informe".replace(",", ".")
     + ". Si se supera, Ollama recorta el contexto en silencio y el modelo ve un informe incompleto. A cambio usa muy poca memoria (~0,1 GB por petición) y es rápido."),
    ("num_gpu", "99", "Número de capas del modelo que se cargan en la GPU.",
     "99 supera las capas del modelo (28 en llama3.2:3b): todo se ejecuta en GPU, la opción más rápida. Si no cupiera en VRAM, Ollama reparte con CPU o falla según la versión."),
    ("low_vram", "False", "Modo de ahorro de VRAM (heredado de llama.cpp).",
     "False prioriza la velocidad. Solo conviene activarlo si te quedas sin memoria de GPU; su efecto depende de la versión de Ollama."),
    ("keep_alive", "'60m'", "Tiempo que el modelo permanece cargado en memoria tras una petición.",
     "Observación: en el código aparece dentro de `options`, donde Ollama no lo reconoce (lo ignora y registra una advertencia). Debe pasarse como argumento de la llamada, `chat(..., keep_alive='60m')`; si no, se aplica el valor por defecto (5 min)."),
]
PARAMS_PROC = [
    ("NUM_HILOS", "4", "Peticiones simultáneas al modelo (requiere OLLAMA_NUM_PARALLEL ≥ hilos en el servidor)."),
    ("TAMAÑO_BLOQUE", "500", "Documentos leídos de MongoDB por iteración; se guardan al terminar cada bloque."),
    ("MAX_REINTENTOS", "3", "Reintentos solo ante fallos de infraestructura (espera 1, 2 y 4 s)."),
    ("timeout", "180 s", "Tiempo máximo de espera por respuesta."),
    ("VERSION_PROCESAMIENTO", "2", "Versión guardada en cada documento; subirla reprocesa todo sin borrar resultados."),
]
PROMPT_REGLAS = [
    ("1. Un bloque por estudio o región", "Fuerza la separación de varios estudios en un mismo informe; es lo más difícil para un modelo pequeño."),
    ("2. Nombre del estudio en mayúsculas", "Normaliza nombres; el código no lo fuerza, depende del modelo."),
    ("3. Hallazgos copiados o resumidos fielmente", "Reduce la invención, pero la salida resulta casi tan larga como la entrada (compite con num_predict)."),
    ("4. Conclusiones = impresión diagnóstica", "Separa descripción y conclusión (base de la clasificación NORMAL / SIN CONCLUSIÓN)."),
    ("5. Sección ausente → lista vacía", "Evita placeholders como «ninguno» o «ver raíz» (el código además los filtra)."),
    ("6. No inventar", "Instrucción de prudencia: no es una garantía."),
]


# ==========================================================
# 5e. COMPONENTES DE INTERFAZ
# ==========================================================
def _cv(df):
    return df.columns[0], df.columns[-1]


def barras(df, cat, val, pct, horizontal=True, color="#0E7490", top=None, altura=None, base=None):
    tot = base if base else df[val].sum()
    d = df[[cat, val]].copy()
    if top:
        d = d.nlargest(top, val)
    d["valor"] = d[val] / tot * 100 if (pct and tot) else d[val]
    if horizontal:
        d = d.sort_values("valor")
        fig = px.bar(d, x="valor", y=cat, orientation="h", color_discrete_sequence=[color])
        fig.update_traces(texttemplate="%{x:.1f}%" if pct else "%{x:,.0f}", textposition="outside")
        fig.update_layout(xaxis_title="% del total" if pct else "Cantidad", yaxis_title=None)
    else:
        fig = px.bar(d, x=cat, y="valor", color_discrete_sequence=[color])
        fig.update_traces(texttemplate="%{y:.1f}%" if pct else "%{y:,.0f}", textposition="outside")
        fig.update_layout(yaxis_title="% del total" if pct else "Cantidad", xaxis_title=None)
    fig.update_layout(height=altura or (max(300, 28 * len(d) + 100) if horizontal else 340), showlegend=False,
                      margin=dict(t=20, b=10, l=10, r=10))
    st.plotly_chart(fig, use_container_width=True)


def donut(df, cat, val, pct, colores=None):
    fig = px.pie(df, names=cat, values=val, hole=0.45, color=cat, color_discrete_map=colores,
                 color_discrete_sequence=px.colors.qualitative.Safe)
    fig.update_traces(textinfo="percent+label" if pct else "value+label")
    fig.update_layout(height=330, margin=dict(t=20, b=10, l=10, r=10), showlegend=False)
    st.plotly_chart(fig, use_container_width=True)


def md_tabla(cab, filas):
    lin = ["| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    lin += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in filas]
    return "\n".join(lin)


# ---------------- Revisión de informes con problemas ----------------
def rv_limpiar(t):
    import html as _h
    t = re.sub(r"(?is)<(script|style).*?</\1>", "", str(t or ""))
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", t)
    t = _h.unescape(re.sub(r"<[^>]+>", " ", t))
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n\n", t)).strip()


def _lst(v):
    return [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])


def rv_reporte(doc):
    r = doc.get("reporte_estructurado")
    return r if isinstance(r, dict) else {}


def rv_estudios(doc):
    est, out = rv_reporte(doc).get("estudios_detallados"), []
    for e in est if isinstance(est, list) else []:
        if isinstance(e, dict):
            out.append({"estudio": str(e.get("estudio") or ""), "hallazgos": _lst(e.get("hallazgos")),
                        "conclusiones": _lst(e.get("conclusiones"))})
        elif isinstance(e, str):
            out.append({"estudio": e, "hallazgos": [], "conclusiones": []})
    return out


@cache_bd(ttl=60)
def rv_motivos():
    r = collection.aggregate([{"$match": {"tiene_problema_estructural": True, "descartado": {"$ne": True}}},
                              {"$group": {"_id": "$motivo_fallo", "n": {"$sum": 1}}}, {"$sort": {"n": -1}},
                              {"$limit": 100}])
    return [(d["_id"], d["n"]) for d in r if isinstance(d["_id"], str)]


def rv_guardar(doc, accion, nuevos=None, meta=None):
    import datetime as dt
    previo = {"fecha": dt.datetime.now(), "accion": accion, "motivo_fallo": doc.get("motivo_fallo"),
              "reporte_estructurado": doc.get("reporte_estructurado"),
              "meta": {k: doc.get(k) for k in ("sexo", "modalidad", "status")}}
    upd = {"$push": {"historial_revision": previo}}
    if accion == "corregido":
        upd["$set"] = {"reporte_estructurado": {**rv_reporte(doc), "estudios_detallados": nuevos},
                       "tiene_problema_estructural": False, "revisado_manualmente": True,
                       "fecha_revision": dt.datetime.now(), **(meta or {})}
        upd["$unset"] = {"motivo_fallo": ""}
    elif accion == "reencolado":
        upd["$set"] = {"procesado_por_ia": False, "tiene_problema_estructural": False}
        upd["$unset"] = {"motivo_fallo": ""}
    else:
        upd["$set"] = {"descartado": True, "fecha_revision": dt.datetime.now()}
    collection.update_one({"_id": doc["_id"]}, upd)
    invalidar_cache()


def ui_revision():
    from bson import ObjectId
    st.subheader("🛠️ Revisión de informes con problema estructural")
    st.caption("Lee el texto original y corrige la estructura guardada en MongoDB. Cada cambio guarda el estado previo en "
               "`historial_revision`.")
    f1, f2, f3 = st.columns([3, 2, 2])
    mots = ["TODOS"] + [m for m, _ in rv_motivos()]
    motivo = f1.selectbox("Motivo de fallo", mots, key="rv_mot", format_func=lambda x: x if x == "TODOS" else x[:90])
    busq = f2.text_input("Buscar en el motivo", key="rv_busq")
    id_txt = f3.text_input("ID exacto", key="rv_id")
    q = {"tiene_problema_estructural": True, "descartado": {"$ne": True}}
    if id_txt.strip():
        try:
            q["_id"] = ObjectId(id_txt.strip())
        except Exception:
            q["_id"] = id_txt.strip()
    if motivo != "TODOS":
        q["motivo_fallo"] = motivo
    elif busq.strip():
        q["motivo_fallo"] = {"$regex": re.escape(busq.strip()), "$options": "i"}
    total = collection.count_documents(q)
    paginas = max((total - 1) // 25 + 1, 1)
    pag = int(st.number_input(f"Página (de {paginas:,}) · {total:,} registros", 1, paginas, 1, key="rv_pag")) - 1
    docs = list(collection.find(q, {"sexo": 1, "modalidad": 1, "status": 1, "motivo_fallo": 1})
                .sort("_id", 1).skip(pag * 25).limit(25))
    if not docs:
        st.success("No hay registros pendientes con estos filtros.")
        return
    st.dataframe(pd.DataFrame([{"ID": str(d["_id"]), "Sexo": d.get("sexo"), "Modalidad": d.get("modalidad"),
                                "Status": d.get("status"), "Motivo": str(d.get("motivo_fallo"))[:140]} for d in docs]),
                 use_container_width=True, hide_index=True, height=240)
    por_id = {d["_id"]: d for d in docs}
    sel = st.selectbox("Registro a revisar", list(por_id), key="rv_sel", format_func=lambda i: (
        f"…{str(i)[-8:]} · {por_id[i].get('modalidad')} · {str(por_id[i].get('motivo_fallo'))[:70]}"))
    doc = collection.find_one({"_id": sel})
    sid = str(sel)
    st.divider()
    izq, der = st.columns(2)
    with izq:
        st.markdown("**📄 Texto original**")
        st.caption(f"Motivo del fallo: {doc.get('motivo_fallo')}")
        st.text_area("Informe (HTML limpiado)", rv_limpiar(doc.get(CAMPO_HTML)), height=480, disabled=True, key=f"rv_o_{sid}")
        with st.expander("HTML crudo"):
            st.code(str(doc.get(CAMPO_HTML) or "")[:20000], language="html")
        with st.expander("Estructura actual guardada"):
            st.json(doc.get("reporte_estructurado") if doc.get("reporte_estructurado") is not None else {})
    with der:
        st.markdown("**✏️ Corrección**")
        act, kn = rv_estudios(doc), f"rv_n_{sid}"
        st.session_state.setdefault(kn, max(len(act), 1))
        b1, b2, _ = st.columns([1, 1, 2])
        if b1.button("➕ Añadir estudio", key=f"rv_add_{sid}"):
            st.session_state[kn] += 1
        if b2.button("➖ Quitar último", key=f"rv_del_{sid}") and st.session_state[kn] > 1:
            st.session_state[kn] -= 1
        nuevos = []
        for i in range(st.session_state[kn]):
            base = act[i] if i < len(act) else {"estudio": "", "hallazgos": [], "conclusiones": []}
            with st.container(border=True):
                nom = st.text_input(f"Estudio {i + 1}", base["estudio"], key=f"rv_e_{sid}_{i}")
                hal = st.text_area("Hallazgos (uno por línea)", "\n".join(base["hallazgos"]), height=100, key=f"rv_h_{sid}_{i}")
                con = st.text_area("Conclusiones (una por línea)", "\n".join(base["conclusiones"]), height=80, key=f"rv_c_{sid}_{i}")
                nuevos.append({"estudio": nom.strip(), "hallazgos": [l.strip() for l in hal.splitlines() if l.strip()],
                               "conclusiones": [l.strip() for l in con.splitlines() if l.strip()]})
        with st.expander("Datos del paciente / informe"):
            m1, m2, m3 = st.columns(3)
            meta = {"sexo": m1.text_input("Sexo", str(doc.get("sexo") or ""), key=f"rv_sx_{sid}"),
                    "modalidad": m2.text_input("Modalidad", str(doc.get("modalidad") or ""), key=f"rv_md_{sid}"),
                    "status": m3.text_input("Status", str(doc.get("status") or ""), key=f"rv_st_{sid}")}
        cambios = {k: v.strip() for k, v in meta.items() if v.strip() != str(doc.get(k) or "").strip()}
        validos = [e for e in nuevos if e["estudio"]]
        ok = st.checkbox("Confirmo que quiero escribir este cambio en MongoDB", key=f"rv_ok_{sid}")
        c1, c2, c3 = st.columns(3)
        try:
            if c1.button("💾 Guardar y marcar corregido", type="primary", disabled=not ok, key=f"rv_g_{sid}"):
                if not validos:
                    st.error("Indica al menos un estudio con nombre.")
                else:
                    rv_guardar(doc, "corregido", validos, cambios)
                    st.session_state.pop(kn, None)
                    st.rerun()
            if c2.button("♻️ Reencolar para la IA", disabled=not ok, key=f"rv_r_{sid}",
                         help="procesado_por_ia=False y sin flag de problema, para que el pipeline lo reprocese."):
                rv_guardar(doc, "reencolado")
                st.rerun()
            if c3.button("🚫 Descartar", disabled=not ok, key=f"rv_d_{sid}", help="Sale de la cola; conserva el flag."):
                rv_guardar(doc, "descartado")
                st.rerun()
        except Exception as e:
            st.error(f"Error al escribir en MongoDB: {e}")


# ---------------- Modelo, prompt y parámetros ----------------
def ui_modelo():
    st.subheader("🤖 Modelo de extracción")
    st.caption("Nota: el modelo es `llama3.2:3b` (3.200 M de parámetros); Llama 3.2 solo existe en 1B y 3B. Los valores reflejan "
               "las opciones reales de la llamada a Ollama; si las cambias en tu script, actualiza el diccionario PROC de este archivo.")
    st.markdown(md_tabla(["Característica", "Valor"], [
        ("Modelo", f"`{PROC['modelo']}` servido localmente con Ollama"),
        ("Tamaño", "~3.200 M de parámetros · ~2 GB cuantizado (4 bits) en disco"),
        ("Familia", "Llama 3.2 (Meta), derivado de Llama 3.1; multilingüe, incluye español"),
        ("Contexto", f"128K tokens nominales; en uso: {PROC['num_ctx']:,}".replace(",", ".")),
        ("Salida", "Texto libre → JSON (`format='json'`) con la estructura `estudios_detallados`"),
        ("Fortalezas", "Rápido, barato, privado (los datos clínicos no salen del equipo), cabe en hardware modesto"),
        ("Debilidades", "Menos fiable que modelos de 7–8B para separar varios estudios, no inventar y seguir reglas largas")]))

    st.subheader("⚙️ Parámetros de la llamada y cómo influyen")
    st.code("format='json',\noptions={'temperature': 0.0, 'num_predict': 512, 'num_ctx': 1024,\n         'num_gpu': 99, 'low_vram': False, 'keep_alive': '60m'}", language="python")
    st.markdown(md_tabla(["Parámetro", "Valor", "Qué significa", "Cómo influye en el procesamiento local"], PARAMS_DOC))
    m = st.columns(4)
    m[0].metric("Prompt (estimado)", f"~{PROMPT_TOKENS} tokens")
    m[1].metric("Espacio para el informe", f"~{TOKENS_INFORME} tokens", f"≈ {CHARS_INFORME:,} caracteres".replace(",", "."), delta_color="off")
    m[2].metric("Salida máxima", f"{PROC['num_predict']} tokens", f"≈ {CHARS_SALIDA:,} caracteres".replace(",", "."), delta_color="off")
    m[3].metric("Memoria de contexto", f"~{KV_MB:.0f} MB", "por petición", delta_color="off")
    st.warning(f"Con num_ctx = {PROC['num_ctx']:,} y num_predict = {PROC['num_predict']}, solo caben unos {CHARS_INFORME:,} caracteres de informe: los más largos se "
               "recortan en silencio y las respuestas largas se cortan, lo que encaja con los «problemas estructurales» que ves en el dashboard "
               "(y con la tasa de problemas creciente según el tamaño del HTML). Subir num_ctx y num_predict cuesta poca memoria con un modelo de 3B."
               .replace(",", "."))
    st.markdown("**Parámetros del proceso** (del script revisado anteriormente; confirma que siguen igual)")
    st.markdown(md_tabla(["Parámetro", "Valor", "Qué hace"], PARAMS_PROC))

    st.subheader("💬 Prompt de sistema")
    st.code(PROMPT_PROC, language="text")
    st.caption("El mensaje de usuario es solo el texto limpio del informe. Con `format='json'` Ollama no impone las claves: si tu prompt real "
               "describe la estructura esperada, añádela aquí. Estructura esperada (la valida el código):")
    st.code(ESQUEMA_PROC, language="json")
    st.markdown(md_tabla(["Regla del prompt", "Para qué sirve"], PROMPT_REGLAS))


# ==========================================================
# 5f. SECCIÓN: DATOS Y PROCESAMIENTO
# ==========================================================
def ui_proceso():
    st.header("⚙️ Datos, extracción y procesamiento")
    st.caption("Estado global de la base y del procesamiento con IA (no depende de los filtros del panel lateral).")
    r = obtener_resumen_global()
    total, proc = r["total"], r["ok"] + r["problema"]
    c = st.columns(5)
    c[0].metric("Total de informes", f"{total:,}")
    c[1].metric("Procesados por IA", f"{proc:,}", f"{div(proc, total):.1%} del total", delta_color="off")
    c[2].metric("Procesados correctamente", f"{r['ok']:,}", f"{div(r['ok'], proc):.1%} de los procesados", delta_color="off")
    c[3].metric("Con problema estructural", f"{r['problema']:,}", f"{div(r['problema'], proc):.1%} de los procesados", delta_color="inverse")
    c[4].metric("Pendientes de procesar", f"{r['pendientes']:,}", f"{div(r['pendientes'], total):.1%} del total", delta_color="off")
    st.progress(min(div(proc, total), 1.0), text=f"Avance del procesamiento: {div(proc, total):.1%} ({proc:,} de {total:,} informes)")
    vista = st.radio("Ver", ["📊 Estado", "🤖 Modelo, prompt y parámetros", "🛠️ Revisión de problemas"],
                     horizontal=True, key="vista_proc", label_visibility="collapsed")

    if vista.startswith("📊"):
        pct = st.radio("Valores", ["% (porcentaje)", "Cantidades"], horizontal=True, key="proc_modo").startswith("%")
        a, b = st.columns(2)
        with a:
            st.subheader("Estado del procesamiento")
            est = pd.DataFrame({"Estado": ["Correctos", "Con problema", "Pendientes"],
                                "Cantidad": [r["ok"], r["problema"], r["pendientes"]]})
            donut(est, "Estado", "Cantidad", pct, {"Correctos": "#10B981", "Con problema": "#EF4444", "Pendientes": "#94A3B8"})
        with b:
            st.subheader("Informes por Status")
            ds = obtener_distribucion("status", "Status")
            if not ds.empty:
                barras(ds, "Status", "Cantidad", pct, color="#6366F1")
        a, b = st.columns(2)
        with a:
            st.subheader("Motivos de fallo de la IA")
            dfm = obtener_motivos_fallo()
            if dfm.empty:
                st.success("Sin fallos registrados.")
            else:
                dfm["Motivo"] = dfm["Motivo"].astype(str).str.replace(r"\d+", "N", regex=True).str.slice(0, 80)
                barras(dfm.groupby("Motivo", as_index=False)["Cantidad"].sum(), "Motivo", "Cantidad", pct, top=8, color="#F59E0B")
        with b:
            st.subheader("Tasa de problemas según el tamaño del HTML")
            dl = obtener_longitud_vs_fallo()
            if not dl.empty and "% problema" in dl.columns:
                dl = dl.copy()
                dl["Rango"] = dl["Rango (caracteres HTML)"].astype(str)
                dl["Tramo"] = dl["Rango"] + "<br>n = " + dl["Informes"].map(lambda v: f"{int(v):,}".replace(",", "."))
                ic = [wilson(int(k), int(n), 0.05) for k, n in zip(dl["Con problema"], dl["Informes"])]
                dl["lo"], dl["hi"] = [c[0] * 100 for c in ic], [c[1] * 100 for c in ic]
                fig = px.bar(dl, x="Tramo", y="% problema", color="% problema", color_continuous_scale="Reds",
                             error_y=dl["hi"] - dl["% problema"], error_y_minus=dl["% problema"] - dl["lo"],
                             category_orders={"Tramo": list(dl["Tramo"])},
                             hover_data={"Informes": ":,", "Con problema": ":,", "% problema": ":.2f", "Tramo": False})
                for _, r_ in dl.iterrows():
                    fig.add_annotation(x=r_["Tramo"], y=r_["hi"], text=f"<b>{r_['% problema']:.1f}%</b>", showarrow=False, yshift=12)
                fig.update_layout(height=380, coloraxis_showscale=False, xaxis_title="Tamaño del HTML (caracteres) · n = informes del tramo",
                                  yaxis_title="% de informes con problema", yaxis_range=[0, float(dl["hi"].max()) * 1.18 + 2],
                                  margin=dict(t=30, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)
                st.caption("Barras = % de informes con problema en cada tramo de tamaño; las líneas negras son el intervalo de confianza del 95 % "
                           "(muy anchas si el tramo tiene pocos informes). Si el % sube con el tamaño, los informes largos desbordan el contexto del modelo.")
        a, b = st.columns(2)
        with a:
            st.subheader("Completitud de campos clave")
            campos = [x for x in ("sexo", "modalidad", "status", CAMPO_HTML, edad_campo, fecha_campo) if x]
            dc = obtener_completitud(campos)
            if not dc.empty:
                cc, cv = _cv(dc)
                fig = px.bar(dc.sort_values(cv), x=cv, y=cc, orientation="h", color_discrete_sequence=["#0E7490"])
                fig.update_traces(texttemplate="%{x:.1f}%", textposition="outside")
                fig.update_layout(height=max(280, 40 * len(dc) + 80), xaxis_range=[0, 105], xaxis_title="% completo",
                                  yaxis_title=None, margin=dict(t=20, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)
        with b:
            st.subheader("Cruce Status × Modalidad")
            dsm = obtener_status_x_modalidad()
            if not dsm.empty:
                p = dsm.pivot_table(index="Status", columns="Modalidad", values="Cantidad", aggfunc="sum", fill_value=0)
                mostrar_heatmap(p, "Status", "Modalidad", pct)
        dv = obtener_versiones()
        if len(dv):
            st.subheader("Versiones de procesamiento")
            st.dataframe(dv, use_container_width=True, hide_index=True)
            st.caption("`modelo_procesamiento` aparece solo si el script lo guarda (recomendado para comparar modelos).")
    elif vista.startswith("🤖"):
        ui_modelo()
    else:
        ui_revision()


# ==========================================================
# 5g. SECCIÓN: DEMOGRAFÍA Y CLÍNICA
# ==========================================================
GRUPOS_EDAD = ["00-17", "18-29", "30-44", "45-59", "60-74", "75+"]


def interpretar_seleccion(rel, nor, neg, pc, pb, ap):
    g = lambda d, k: d.get(k, 0.0)
    pr, pn, po, ps = g(pc, "RELEVANTE"), g(pc, "NORMAL"), g(pc, "OTROS HALLAZGOS"), g(pc, "SIN CONCLUSIÓN")
    L = []
    if pr < 5:
        L.append(f"**RELEVANTE = {pr:.1f} %: criterio muy selectivo.** Con pocos términos o muy específicos (graves/urgentes) se gana "
                 "especificidad —casi todo lo marcado es importante— pero se pierde sensibilidad: hallazgos que sí importan quedan en «OTROS».")
    elif pr <= 25:
        L.append(f"**RELEVANTE = {pr:.1f} %: criterio intermedio.** Suele ser un equilibrio razonable entre sensibilidad y especificidad; "
                 "conviene contrastarlo con una revisión clínica de una muestra.")
    else:
        L.append(f"**RELEVANTE = {pr:.1f} %: criterio muy sensible.** Probablemente incluye hallazgos frecuentes o inespecíficos "
                 "(falsos positivos): «relevante» pierde capacidad de discriminar y se acerca a «tiene algún hallazgo».")
    d = pr - g(pb, "RELEVANTE")
    if abs(d) >= 0.5:
        L.append(f"Frente a la lista por defecto, tu selección {'amplía' if d > 0 else 'reduce'} lo relevante en **{abs(d):.1f} puntos**: "
                 f"{'añade términos que antes quedaban fuera o quita la restricción de negaciones.' if d > 0 else 'es más estricta que la lista por defecto.'}")
    else:
        L.append("Tu selección da un resultado similar al de la lista por defecto.")
    if len(rel) <= 3:
        L.append(f"Solo hay **{len(rel)} término(s)** relevantes: el resultado depende casi por completo de ellos; úsalo para estudiar un problema concreto, no «lo relevante» en general.")
    elif len(rel) > 40:
        L.append(f"Con **{len(rel)} términos** es fácil que alguno inespecífico domine la clasificación; revisa el aporte de cada término.")
    if ap is not None and not ap.empty:
        top = ap.sort_values("Estudios", ascending=False).iloc[0]
        L.append(f"El término que más estudios dispara es **«{top['Término']}»** ({top['% de estudios']:.1f} % de los estudios): si es un hallazgo "
                 "frecuente o de poca gravedad, está inflando RELEVANTE.")
    if not neg:
        L.append("Con **«ignorar negaciones» desactivado**, frases como «sin evidencia de fractura» cuentan como relevantes: el % tiende a inflarse.")
    if len(nor) < 3:
        L.append(f"Solo **{len(nor)} indicador(es) de normalidad**: criterio estricto; NORMAL ({pn:.1f} %) se subestima y más estudios pasan a «OTROS».")
    elif len(nor) > 12:
        L.append(f"Con **{len(nor)} indicadores de normalidad** el criterio es laxo: NORMAL ({pn:.1f} %) puede incluir conclusiones que niegan algo irrelevante.")
    L.append(f"**OTROS HALLAZGOS = {po:.1f} %**: lo que ninguna de las dos listas reconoce (cuanto mayor, menos cubren tus listas). "
             f"**SIN CONCLUSIÓN = {ps:.1f} %** depende de la calidad del procesamiento, no de las palabras que elijas.")
    return L


GUIA_HALLAZGOS = [
    ("Hallazgos graves/urgentes (hemorragia, neumotórax, embolia, disección, isquemia…)", "% relevante bajo; alta especificidad",
     "«Relevante» ≈ patología crítica. Mide urgencia, pero infraestima el resto de patología."),
    ("Hallazgos oncológicos (masa, tumor, neoplasia, metástasis, nódulo)", "% medio-bajo",
     "«Relevante» ≈ sospecha oncológica. Útil para seguimiento oncológico; ojo con «masa»/«nódulo» benignos."),
    ("Inflamatorios/infecciosos y colecciones (absceso, consolidación, derrame)", "% medio",
     "«Relevante» ≈ proceso agudo o complicación; «derrame» leve infla el resultado."),
    ("Hallazgos frecuentes de bajo riesgo (degenerativos, quistes simples, osteofitos…)", "% alto; baja especificidad",
     "«Relevante» ≈ «tiene algún hallazgo»: deja de distinguir lo importante."),
    ("Pocos términos", "Resultado muy dependiente de ellos", "Sirve para estudiar un problema concreto, no para clasificar en general."),
    ("Muchos términos", "% alto y difuso", "Alta sensibilidad, baja especificidad; revisa el aporte de cada término."),
    ("Indicadores de normalidad estrictos", "NORMAL baja, OTROS sube", "Etiqueta como normal solo lo explícito; más prudente."),
    ("Indicadores de normalidad laxos", "NORMAL sube", "Mayor riesgo de dar por normal algo que solo niega un hallazgo irrelevante."),
    ("Ignorar negaciones: activado / desactivado", "Desactivado → RELEVANTE sube", "«Sin evidencia de fractura» cuenta como fractura si se desactiva."),
]


def ui_clasificacion(f, pct):
    st.subheader("🩺 Clasificación clínica por hallazgos")
    st.caption("Elige qué hallazgos consideras RELEVANTES y qué indicadores cuentan como NORMALES: la clasificación se recalcula sobre todos los "
               "estudios. Es una heurística, no un diagnóstico. La selección se mantiene en las demás secciones.")
    cat = obtener_catalogo_hallazgos(f)
    g_rel = st.session_state.get("kw_rel", list(RELEVANTES_DEFECTO))
    g_nor = st.session_state.get("kw_norm", list(NORMALES_DEFECTO))
    frec = [h.lower() for h in cat["Hallazgo"]] if not cat.empty else []
    opciones = list(dict.fromkeys(g_rel + list(RELEVANTES_DEFECTO) + frec))
    corto = lambda x: x if len(x) <= 90 else x[:87] + "…"

    def _reset():
        for k in ("kw_rel", "kw_norm", "kw_neg", "w_rel", "w_norm", "w_neg", "w_extra"):
            st.session_state.pop(k, None)

    c1, c2 = st.columns([3, 2])
    with c1:
        sel = st.multiselect("Hallazgos RELEVANTES (términos por defecto + hallazgos más frecuentes de tus datos)", opciones,
                             default=g_rel, key="w_rel", format_func=corto)
        extra = st.text_input("Añadir términos propios (separados por comas)", key="w_extra")
    with c2:
        sel_n = st.multiselect("Indicadores de NORMALIDAD (se buscan en las conclusiones)",
                               list(dict.fromkeys(g_nor + list(NORMALES_DEFECTO))), default=g_nor, key="w_norm")
        neg = st.checkbox("Ignorar menciones negadas («sin evidencia de…»)", value=st.session_state.get("kw_neg", True), key="w_neg")
        st.button("↩️ Restaurar valores por defecto", on_click=_reset)
    rel = tuple(sorted({k.strip().lower() for k in list(sel) + extra.split(",") if k.strip()}))
    nor = tuple(sorted({k.strip().lower() for k in sel_n if k.strip()}))
    st.session_state.update(kw_rel=list(rel), kw_norm=list(nor), kw_neg=neg)
    fc = {**f, "relevantes": rel, "normales": nor, "negacion": neg}
    if not rel:
        st.info("Elige al menos un hallazgo relevante.")
        return fc

    dfc = obtener_clasificacion_total(fc)
    if dfc.empty:
        st.info("Sin estudios bajo los filtros actuales.")
        return fc
    f_def = {**f, "relevantes": tuple(sorted(k.lower() for k in RELEVANTES_DEFECTO)),
             "normales": tuple(sorted(k.lower() for k in NORMALES_DEFECTO)), "negacion": True}
    dfb = dfc if f_def == fc else obtener_clasificacion_total(f_def)
    tot, totb = dfc["Cantidad"].sum(), dfb["Cantidad"].sum()
    v, vb = dict(zip(dfc["Clasificación"], dfc["Cantidad"])), dict(zip(dfb["Clasificación"], dfb["Cantidad"]))
    pc = {k: div(v.get(k, 0), tot) * 100 for k in ORDEN_CLASIF}
    pb = {k: div(vb.get(k, 0), totb) * 100 for k in ORDEN_CLASIF}
    for col, nombre in zip(st.columns(4), ORDEN_CLASIF):
        col.metric(nombre.capitalize(), f"{pc[nombre]:.1f} %" if pct else f"{v.get(nombre, 0):,}",
                   f"{pc[nombre] - pb[nombre]:+.1f} pp vs lista por defecto" if f_def != fc else f"{v.get(nombre, 0):,} estudios",
                   delta_color="off")
    a, b = st.columns(2)
    with a:
        st.markdown("**Distribución de la clasificación**")
        donut(dfc, "Clasificación", "Cantidad", pct, COLORES_CLASIF)
    ap = None
    with b:
        st.markdown("**¿Qué términos disparan RELEVANTE?**")
        if st.checkbox("Calcular el aporte de cada término (consulta adicional)", key="cl_aporte"):
            ap = obtener_aporte_terminos(fc, rel[:25])
            if not ap.empty:
                barras(ap, "Término", "Estudios", pct, top=15, color="#EF4444", base=tot)
                st.caption("% de estudios que contienen cada término (los términos pueden solaparse; se muestran como máximo 25 términos).")
        else:
            st.caption("Activa la casilla para ver qué porcentaje de estudios dispara cada término seleccionado.")

    st.markdown("**Desglose de la clasificación**")
    dims = [d for d in ("Estudio", "Sexo", "Modalidad", "Grupo de edad", "Mes") if d in EJES]
    x1, x2 = st.columns([2, 1])
    dim = x1.selectbox("Desglosar por", dims, key="cl_dim")
    lim = x2.slider("Máx. categorías", 5, 40, 15, key="cl_lim") if dim == "Estudio" else 60
    p = tabla_contingencia(fc, dim, "Clasificación", lim, 6)
    if not p.empty:
        cols = [c for c in ORDEN_CLASIF if c in p.columns]
        p = p[cols]
        datos = p.div(p.sum(axis=1).replace(0, 1), axis=0) * 100 if pct else p
        if dim == "Estudio" and "RELEVANTE" in datos.columns:
            datos = datos.sort_values("RELEVANTE")
        if dim == "Mes":
            fig = px.line(datos, x=datos.index, y=cols, markers=True, color_discrete_map=COLORES_CLASIF)
        elif dim in EJES_ORDEN_NATURAL:
            fig = px.bar(datos, x=datos.index, y=cols, barmode="stack", color_discrete_map=COLORES_CLASIF)
        else:
            fig = px.bar(datos, x=cols, y=datos.index, orientation="h", barmode="stack", color_discrete_map=COLORES_CLASIF)
        fig.update_layout(height=max(360, 26 * len(datos) + 120) if dim not in EJES_ORDEN_NATURAL else 380,
                          legend_title_text="", xaxis_title=None, yaxis_title="% del total" if pct else "Cantidad",
                          margin=dict(t=20, b=10, l=10, r=10))
        if dim not in EJES_ORDEN_NATURAL:
            fig.update_layout(xaxis_title="% del total" if pct else "Cantidad", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("#### 🧭 Qué significa tu selección")
    for linea in interpretar_seleccion(rel, nor, neg, pc, pb, ap):
        st.markdown(f"- {linea}")
    with st.expander("📖 Guía: qué significa según los hallazgos que elijas"):
        st.markdown(md_tabla(["Si eliges…", "Efecto esperado", "Interpretación"], GUIA_HALLAZGOS))
        st.caption("Regla práctica: más términos o más inespecíficos = más sensibilidad y menos especificidad. Valida siempre con una muestra revisada por un clínico.")
    return fc


def ui_demo_clinica():
    from plotly.subplots import make_subplots
    st.header("👥 Demografía y 🧠 clínica")
    pct = st.radio("Mostrar valores en", ["% (porcentaje)", "Cantidades"], horizontal=True, key="modo_vals").startswith("%")
    st.caption("Los porcentajes se calculan sobre el total de cada gráfico. Demografía de edad y actividad: toda la base; sexo y el apartado clínico: respetan los filtros del panel lateral.")

    # ---------------- DEMOGRAFÍA ----------------
    st.subheader("👥 Demografía")
    a, b = st.columns(2)
    with a:
        st.markdown("**Sexo**")
        ds = obtener_demografia(f)
        if not ds.empty:
            donut(ds, "Sexo", "Cantidad", pct)
    with b:
        st.markdown("**Grupo de edad**")
        if edad_campo:
            de, media = obtener_edad_global()
            if media is not None:
                st.caption(f"Edad media: {media:.1f} años")
            de = de[de["Grupo"] != "SIN DATO"]
            if not de.empty:
                barras(de, "Grupo", "Cantidad", pct, horizontal=False, color="#10B981")
    if edad_campo:
        pir = obtener_piramide()
        if not pir.empty:
            st.markdown("**Edad por sexo** (en %, cada sexo suma 100 %: compara distribuciones, no volúmenes)" if pct else "**Edad por sexo**")
            pir = pir.copy()
            pir["valor"] = pir.groupby("Sexo")["Cantidad"].transform(lambda s: s / s.sum() * 100) if pct else pir["Cantidad"]
            fig = px.bar(pir, x="Grupo", y="valor", color="Sexo", barmode="group", category_orders={"Grupo": GRUPOS_EDAD})
            fig.update_layout(height=340, yaxis_title="% dentro de cada sexo" if pct else "Cantidad", xaxis_title=None,
                              margin=dict(t=20, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
    a, b = st.columns(2)
    with a:
        if fecha_campo:
            st.markdown("**Volumen mensual**")
            dm = obtener_volumen_mensual().sort_values("Mes")
            if not dm.empty:
                dm = dm.copy()
                dm["valor"] = dm["Informes"] / dm["Informes"].sum() * 100 if pct else dm["Informes"]
                fig = px.line(dm, x="Mes", y="valor", markers=True, color_discrete_sequence=["#0E7490"])
                fig.update_layout(height=340, yaxis_title="% del total" if pct else "Informes", xaxis_title=None,
                                  margin=dict(t=20, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)
                st.caption("El último mes puede estar incompleto.")
    with b:
        if edad_campo:
            st.markdown("**Edad media por modalidad**")
            dmo = obtener_edad_por_modalidad()
            if not dmo.empty:
                fig = px.bar(dmo.sort_values("Edad media"), x="Edad media", y="Modalidad", orientation="h",
                             hover_data=["Informes"], color_discrete_sequence=["#8B5CF6"])
                fig.update_traces(texttemplate="%{x:.1f}", textposition="outside")
                fig.update_layout(height=max(300, 28 * len(dmo) + 100), yaxis_title=None, xaxis_title="Años",
                                  margin=dict(t=20, b=10, l=10, r=10))
                st.plotly_chart(fig, use_container_width=True)

    # ---------------- CLÍNICA ----------------
    st.divider()
    st.subheader("🧠 Clínica")
    k = obtener_kpis_clinicos(f)
    m = st.columns(5)
    m[0].metric("Informes", f"{k.get('informes', 0):,}")
    m[1].metric("Estudios", f"{k.get('estudios', 0):,}")
    m[2].metric("Hallazgos", f"{k.get('hallazgos', 0):,}")
    m[3].metric("Conclusiones", f"{k.get('conclusiones', 0):,}")
    m[4].metric("Informes con varios estudios", f"{div(k.get('multi', 0), k.get('informes', 0)):.1%}")
    a, b = st.columns(2)
    with a:
        st.markdown("**Modalidades**")
        dmod = obtener_distribucion("modalidad", "Modalidad")
        if not dmod.empty:
            barras(dmod, "Modalidad", "Cantidad", pct, color="#0E7490", top=12)
    with b:
        st.markdown("**Estudios por informe**")
        dh = obtener_hist_estudios_por_informe(f)
        if not dh.empty:
            dh = dh.copy()
            dh["N"] = dh["N"].astype(str)
            dh["valor"] = dh["Informes"] / dh["Informes"].sum() * 100 if pct else dh["Informes"]
            dh["etiqueta"] = dh["valor"].map(lambda v: f"{v:.1f}%" if pct else f"{int(v):,}")
            logy = st.checkbox("Escala logarítmica (para ver mejor los valores pequeños)", key="hist_log")
            fig = px.bar(dh, x="N", y="valor", text="etiqueta", color_discrete_sequence=["#6366F1"],
                         category_orders={"N": list(dh["N"])}, hover_data={"Informes": ":,", "valor": False, "etiqueta": False})
            fig.update_traces(textposition="outside", cliponaxis=False)
            fig.update_layout(height=320, xaxis_title="Estudios distintos en el informe", yaxis_title="% de informes" if pct else "Informes",
                              margin=dict(t=20, b=10, l=10, r=10), showlegend=False)
            fig.update_yaxes(type="log" if logy else "linear", **({} if logy else {"range": [0, float(dh["valor"].max()) * 1.15]}))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Número de estudios distintos por informe (nombre unificado y sin los bloques que son hallazgos). "
                       "Casi todos los informes tienen un solo estudio: usa la escala logarítmica para comparar el resto.")
    st.markdown("**Estudios más solicitados y concentración (Pareto)**")
    dist = obtener_distribucion_estudios(f)
    if not dist.empty:
        cc, cv = _cv(dist)
        dist = dist.sort_values(cv, ascending=False).reset_index(drop=True)
        tot_e = dist[cv].sum()
        dist["pct"] = dist[cv] / tot_e * 100
        dist["acum"] = dist["pct"].cumsum()
        top = dist.head(15)
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_bar(x=top[cc], y=top["pct"] if pct else top[cv], name="% del total" if pct else "Cantidad", marker_color="#6366F1")
        fig.add_scatter(x=top[cc], y=top["acum"], name="% acumulado", mode="lines+markers", marker_color="#F59E0B", secondary_y=True)
        fig.update_yaxes(title_text="% del total" if pct else "Cantidad", secondary_y=False)
        fig.update_yaxes(title_text="% acumulado", range=[0, 100], secondary_y=True)
        fig.update_layout(height=400, margin=dict(t=20, b=10, l=10, r=10), legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, use_container_width=True)
        n80 = int((dist["acum"] >= 80).idxmax()) + 1 if (dist["acum"] >= 80).any() else len(dist)
        st.caption(f"{n80} estudios acumulan el 80 % de la actividad (entre los {len(dist)} estudios más frecuentes).")
    with st.expander("🧹 Cómo se unifican los nombres de estudio (verificación)"):
        st.caption("Para que cada examen aparezca una sola vez («RAYOS X DE TÓRAX», «RX DE TORAX» y «RADIOGRAFÍA DE TÓRAX» = «RADIOGRAFÍA DE TÓRAX») "
                   "y no se cuenten como estudios los hallazgos que el modelo convirtió en bloque («LORDOSIS NORMAL DE LA COLUMNA LUMBAR»…). "
                   "Esta normalización se aplica a TODOS los gráficos, cruces, co-ocurrencia, contrastes y al filtro «Tipo de estudio». "
                   "Los hallazgos de esos bloques excluidos tampoco se cuentan. Las reglas se editan en NO_ESTUDIO_RX, MODALIDADES_ESTUDIO y REGIONES_ESTUDIO.")
        crudos = obtener_nombres_estudio_crudos(f)
        if crudos.empty:
            st.info("Sin datos.")
        else:
            crudos = crudos.assign(Unificado=crudos["Nombre"].map(canon_estudio_py))
            excl = crudos[crudos["Unificado"].isna()]
            st.markdown(f"**Bloques excluidos por ser hallazgos:** {excl['Cantidad'].sum():,} ({excl['Cantidad'].sum() / crudos['Cantidad'].sum():.1%} de los bloques), {len(excl):,} nombres distintos")
            if not excl.empty:
                st.dataframe(excl.head(15)[["Nombre", "Cantidad"]], use_container_width=True, hide_index=True)
            grp = crudos.dropna(subset=["Unificado"]).groupby("Unificado")
            filas = [{"Nombre unificado": u, "Variantes": len(g), "Total": int(g["Cantidad"].sum()),
                      "Ejemplos": "; ".join(f"{n[:45]} ({c:,})" for n, c in zip(g.sort_values("Cantidad", ascending=False)["Nombre"][:4], g.sort_values("Cantidad", ascending=False)["Cantidad"][:4]))}
                     for u, g in grp if len(g) > 1]
            st.markdown(f"**Nombres unificados** ({len(filas):,} estudios agrupan varias variantes; se muestran los más frecuentes):")
            if filas:
                st.dataframe(pd.DataFrame(filas).sort_values("Total", ascending=False).head(20), use_container_width=True, hide_index=True)
    a, b = st.columns(2)
    for col, campo, titulo, color in ((a, "hallazgos", "Hallazgos más frecuentes", "#EF4444"), (b, "conclusiones", "Conclusiones más frecuentes", "#6366F1")):
        with col:
            st.markdown(f"**{titulo}**")
            dv = obtener_top_valores(f, campo)
            if not dv.empty:
                cc, cv = _cv(dv)
                barras(dv, cc, cv, pct, color=color, base=k.get("estudios") or None)
                st.caption("% sobre el total de estudios." if pct else "")

    # ---------------- CLASIFICACIÓN ----------------
    st.divider()
    fc = ui_clasificacion(f, pct)

    # ---------------- RELACIONES ----------------
    st.divider()
    st.subheader("🔀 Relaciones entre variables")
    st.caption("Cruce de cualquier par de campos (la clasificación usa tu selección de hallazgos). En % cada fila suma 100 %.")
    nombres = list(EJES)
    c1, c2, c3, c4 = st.columns(4)
    ea = c1.selectbox("Filas", nombres, index=0, key="cr_a")
    eb = c2.selectbox("Columnas", nombres, index=min(1, len(nombres) - 1), key="cr_b")
    tf = c3.slider("Máx. filas", 5, 30, 10, key="cr_f")
    tc_ = c4.slider("Máx. columnas", 5, 30, 10, key="cr_c")
    if ea == eb:
        st.info("Elige dos campos distintos.")
    else:
        pv = construir_pivot(obtener_cruce(fc, ea, eb), ea, eb, tf, tc_)
        mostrar_heatmap(pv, ea, eb, pct)
        if not pv.empty:
            with st.expander("Ver tabla y descargar"):
                st.dataframe(pv, use_container_width=True)
                st.download_button("📥 Descargar (CSV)", pv.to_csv().encode("utf-8"), file_name="cruce.csv", mime="text/csv")

    st.subheader("🤝 Estudios que se solicitan juntos")
    pares, totales, n_multi = obtener_coocurrencia(f)
    if not pares:
        st.info("No hay informes con varios estudios bajo los filtros actuales.")
    else:
        st.caption(f"{n_multi:,} informes multi-estudio analizados" + (" (muestra de 100.000)" if n_multi >= 100000 else "")
                   + ". En % = porcentaje de esos informes en los que aparece la pareja.")
        top_n = st.slider("Estudios en la matriz", 5, 20, 12, key="co_n")
        top_e = [e for e, _ in Counter(totales).most_common(top_n)]
        mat = pd.DataFrame(0.0, index=top_e, columns=top_e)
        for (x, y), n in pares.items():
            if x in mat.index and y in mat.columns:
                mat.loc[x, y] = mat.loc[y, x] = n / n_multi * 100 if pct else n
        fig = px.imshow(mat, text_auto=".1f" if pct else True, aspect="auto", color_continuous_scale="Purples")
        fig.update_layout(height=max(400, 36 * top_n + 120), margin=dict(t=20, b=10, l=10, r=10))
        st.plotly_chart(fig, use_container_width=True)
        dp = pd.DataFrame([{"Estudio A": x, "Estudio B": y, "Informes": n, "% de informes": n / n_multi * 100}
                           for (x, y), n in pares.items()]).sort_values("Informes", ascending=False).head(20)
        with st.expander("Pares más frecuentes (tabla y descarga)"):
            st.dataframe(dp.round(2), use_container_width=True, hide_index=True)
            st.download_button("📥 Descargar pares (CSV)", dp.to_csv(index=False).encode("utf-8"), file_name="pares_estudios.csv", mime="text/csv")


# ==========================================================
# 5h. SECCIÓN: INFERENCIAL (hipótesis + interpretación)
# ==========================================================
def decision(p, alfa):
    return f"**Se rechaza H₀** (p {fmt_p(p)} < α = {alfa})" if p < alfa else f"**No se rechaza H₀** (p {fmt_p(p)} ≥ α = {alfa})"


def ui_inferencial():
    st.header("📐 Análisis inferencial")
    st.caption("Contrastes sobre TODOS los estudios filtrados (agregación en MongoDB). Con cientos de miles de registros casi todo es "
               "«significativo»: la decisión se apoya en el tamaño del efecto. La unidad es el estudio; varios estudios del mismo paciente o "
               "informe no son independientes, así que los p-valores son optimistas.")
    with st.expander("📖 Cómo leer un contraste de hipótesis"):
        st.markdown("- **H₀ (hipótesis nula):** «no hay efecto / no hay diferencia / no hay relación».  **H₁:** «sí lo hay».\n"
                    "- **p-valor:** probabilidad de ver datos tan extremos si H₀ fuera cierta. Si **p < α** se rechaza H₀; si no, simplemente no hay evidencia suficiente (no prueba que H₀ sea cierta).\n"
                    "- **α:** riesgo aceptado de rechazar H₀ siendo cierta (habitual 0,05).\n"
                    "- **Tamaño del efecto** (V de Cramér, OR, η²): cuánto de grande es la diferencia; es lo que decide si importa en la práctica.\n"
                    "- **Intervalo de confianza:** rango plausible del valor real; intervalos que no se solapan sugieren diferencias.")
    alfa = st.select_slider("Nivel de significación α", [0.10, 0.05, 0.01, 0.001], value=0.05)
    st.caption(f"Los contrastes con la clasificación RELEVANTE usan tu selección de hallazgos ({len(f['relevantes'])} términos relevantes, "
               f"{len(f['normales'])} de normalidad). Cámbiala en «Demografía y clínica».")
    dims = [x for x in ("Estudio", "Sexo", "Modalidad", "Grupo de edad", "Mes") if x in EJES]
    t1, t2, t3, t4 = st.tabs(["🔗 Asociación (χ²)", "📊 Tasa de RELEVANTES", "🎂 Edad (ANOVA)", "📈 Tendencia temporal"])

    with t1:
        st.info("**H₀:** las variables A y B son independientes.  **H₁:** están asociadas (la distribución de B cambia según A).")
        c1, c2 = st.columns(2)
        ea = c1.selectbox("Variable A", dims, key="in_a")
        eb = c2.selectbox("Variable B", ["Clasificación"] + [x for x in dims if x != ea], key="in_b")
        r = chi2_asociacion(tabla_contingencia(f, ea, eb))
        if r is None:
            st.info("Datos insuficientes para este cruce.")
        else:
            m = st.columns(5)
            m[0].metric("N", f"{int(r['n']):,}")
            m[1].metric("χ²", f"{r['chi2']:,.1f}", f"gl = {r['gl']}", delta_color="off")
            m[2].metric("p-valor", fmt_p(r["p"]))
            m[3].metric("V de Cramér", f"{r['v']:.3f}", magnitud_v(r["v"]), delta_color="off")
            m[4].metric("Celdas con E<5", f"{r['pct_esp_bajo']:.0%}")
            st.markdown(f"**Interpretación.** {decision(r['p'], alfa)}: "
                        + (f"hay asociación entre {ea} y {eb}, de magnitud **{magnitud_v(r['v'])}** (V = {r['v']:.3f})."
                           if r["p"] < alfa else f"no hay evidencia de asociación entre {ea} y {eb}.")
                        + (" Una asociación grande entre variables que dependen una de otra (p. ej. estudio y modalidad) es casi obvia y no aporta información nueva." if r["v"] >= .5 else "")
                        + (" Más del 20 % de celdas con frecuencia esperada < 5: reduce categorías para fiarte del χ²." if r["pct_esp_bajo"] > .2 else ""))
            fig = px.imshow(r["res"], text_auto=".1f", aspect="auto", color_continuous_scale="RdBu_r", color_continuous_midpoint=0)
            fig.update_layout(height=max(350, 30 * len(r["res"]) + 120), margin=dict(t=10, b=10, l=10, r=10))
            st.plotly_chart(fig, use_container_width=True)
            st.caption("Residuos ajustados: |z| > 2 = combinación más (rojo) o menos (azul) frecuente de lo esperado si no hubiera relación.")

    with t2:
        st.info("**H₀:** la proporción de estudios RELEVANTES del segmento es igual a la del resto.  **H₁:** es distinta.")
        c1, c2 = st.columns([2, 1])
        dim = c1.selectbox("Segmentar por", dims, key="in_seg")
        min_n = c2.slider("Mínimo de estudios por segmento", 10, 1000, 100, key="in_min")
        p = tabla_contingencia(f, dim, "Clasificación", 60, 4)
        if p.empty or "RELEVANTE" not in p.columns:
            st.info("No hay estudios RELEVANTES bajo estos filtros.")
        else:
            ni, ki = p.sum(axis=1), p["RELEVANTE"]
            N, K = ni.sum(), ki.sum()
            filas = []
            for s in p.index[(ni >= min_n) & (p.index != "OTROS")]:
                lo, hi = wilson(ki[s], ni[s], alfa)
                pv_, orr, olo, ohi = prop_vs_resto(ki[s], ni[s], K, N)
                filas.append({"Segmento": s, "Estudios": int(ni[s]), "Relevantes": int(ki[s]), "% relevante": ki[s] / ni[s] * 100,
                              "IC inf %": lo * 100, "IC sup %": hi * 100, "OR vs resto": orr, "OR inf": olo, "OR sup": ohi, "p": pv_})
            t = pd.DataFrame(filas).dropna(subset=["p"]) if filas else pd.DataFrame()
            if t.empty:
                st.info("Ningún segmento alcanza el mínimo de estudios.")
            else:
                t["p Holm"] = holm(t["p"])
                t["Significativo"] = t["p Holm"] < alfa
                t = t.sort_values("% relevante")
                st.metric("Tasa global de RELEVANTES", f"{K / N:.2%}", f"IC: {wilson(K, N, alfa)[0]:.2%} – {wilson(K, N, alfa)[1]:.2%}", delta_color="off")
                sig = t[t["Significativo"]]
                hi_, lo_ = t.iloc[-1], t.iloc[0]
                st.markdown(f"**Interpretación.** Tras la corrección de Holm, **{len(sig)} de {len(t)}** segmentos difieren del resto (se rechaza H₀). "
                            f"El mayor % es {hi_['Segmento']} ({hi_['% relevante']:.1f} %, OR {hi_['OR vs resto']:.2f}) y el menor {lo_['Segmento']} "
                            f"({lo_['% relevante']:.1f} %, OR {lo_['OR vs resto']:.2f}). OR > 1: más relevantes que el resto; OR < 1: menos.")
                fig = px.scatter(t, x="% relevante", y="Segmento", color="Significativo", error_x=t["IC sup %"] - t["% relevante"],
                                 error_x_minus=t["% relevante"] - t["IC inf %"], color_discrete_map={True: "#EF4444", False: "#94A3B8"})
                fig.add_vline(x=K / N * 100, line_dash="dash", line_color="#334155")
                fig.update_layout(height=max(350, 28 * len(t) + 120), margin=dict(t=10, b=10, l=10, r=10), yaxis_title=None)
                st.plotly_chart(fig, use_container_width=True)
                tabla = t.drop(columns=["IC inf %", "IC sup %"]).round(4)
                with st.expander("Ver tabla y descargar"):
                    st.dataframe(tabla, use_container_width=True, hide_index=True)
                    st.download_button("📥 Descargar (CSV)", tabla.to_csv(index=False).encode("utf-8"), file_name="inferencia_proporciones.csv")

    with t3:
        st.info("**H₀:** la edad media es igual en todos los grupos (μ₁ = μ₂ = … = μₖ).  **H₁:** al menos un grupo tiene una media distinta.")
        if not edad_campo:
            st.info("No se detectó el campo de edad.")
        else:
            dimE = st.selectbox("Comparar la edad media entre", [x for x in ("Sexo", "Modalidad", "Clasificación", "Estudio") if x in EJES], key="in_edad")
            d = obtener_edad_resumen(f, dimE)
            a = anova_desde_resumen(d) if not d.empty else None
            if a is None:
                st.info("Se necesitan al menos 2 grupos con ≥ 30 estudios y edad válida.")
            else:
                m = st.columns(4)
                m[0].metric("Grupos / N", f"{a['k']} / {a['N']:,}")
                m[1].metric("ANOVA F", f"{a['F']:,.1f}", f"p {fmt_p(a['p'])}", delta_color="off")
                m[2].metric("Welch F", f"{a['Fw']:,.1f}", f"p {fmt_p(a['pw'])}", delta_color="off")
                m[3].metric("η² (efecto)", f"{a['eta2']:.3f}", "pequeño <0.06" if a["eta2"] < 0.06 else "medio/grande", delta_color="off")
                mag = "pequeño" if a["eta2"] < .06 else "mediano" if a["eta2"] < .14 else "grande"
                st.markdown(f"**Interpretación.** {decision(a['pw'], alfa)} (Welch): "
                            + (f"la edad media difiere entre grupos de {dimE}, pero el efecto es **{mag}** (η² = {a['eta2']:.3f}: el grupo explica el {a['eta2']:.1%} de la variación de la edad)."
                               if a["pw"] < alfa else f"no hay evidencia de que la edad media difiera entre grupos de {dimE}."))
                d = d.copy()
                d["IC95"] = sps.t.ppf(0.975, d["n"] - 1) * d["sd"] / np.sqrt(d["n"])
                fig = px.scatter(d.sort_values("m"), x="m", y="Grupo", error_x="IC95", color_discrete_sequence=["#8B5CF6"])
                fig.update_layout(height=max(320, 28 * len(d) + 120), margin=dict(t=10, b=10, l=10, r=10), xaxis_title="Edad media (IC 95 %)", yaxis_title=None)
                st.plotly_chart(fig, use_container_width=True)

    with t4:
        st.info("**H₀:** no hay tendencia lineal en la proporción mensual de estudios RELEVANTES (pendiente = 0).  **H₁:** la proporción sube o baja de forma sostenida.")
        if not fecha_campo:
            st.info("No se detectó el campo de fecha.")
        else:
            excl = st.checkbox("Excluir el último mes (puede estar incompleto)", value=True, key="in_excl")
            f_t = {**f, "estudio": "TODOS"}
            if f["estudio"] != "TODOS":
                st.caption("ℹ️ Esta pestaña ignora el filtro «Tipo de estudio».")
            p = tabla_contingencia(f_t, "Mes", "Clasificación", 300, 4)
            if p.empty or "RELEVANTE" not in p.columns:
                st.info("Sin datos suficientes.")
            else:
                p = p.drop(index="SIN FECHA", errors="ignore").sort_index()
                p = p.iloc[:-1] if excl and len(p) > 1 else p
                p = p[p.sum(axis=1) >= 30]
                if len(p) < 4:
                    st.info("Se necesitan al menos 4 meses con ≥ 30 estudios.")
                else:
                    n_m, k_m = p.sum(axis=1), p["RELEVANTE"]
                    z, pv_, pend = tendencia_ca(k_m.values, n_m.values)
                    m = st.columns(3)
                    m[0].metric("Z Cochran-Armitage", f"{z:.2f}")
                    m[1].metric("p-valor", fmt_p(pv_))
                    m[2].metric("Pendiente", f"{pend:+.3f} pp/mes")
                    st.markdown(f"**Interpretación.** {decision(pv_, alfa)}: "
                                + (f"la proporción de relevantes {'aumenta' if pend > 0 else 'disminuye'} de forma sostenida, unos {abs(pend):.3f} puntos porcentuales por mes."
                                   if pv_ < alfa else "no se detecta una tendencia lineal; los cambios mensuales son compatibles con variación aleatoria.")
                                + " (El test solo detecta tendencias lineales, no estacionalidad ni cambios bruscos.)")
                    ic = [wilson(k, n, alfa) for k, n in zip(k_m, n_m)]
                    tt = pd.DataFrame({"Mes": p.index, "% relevante": k_m.values / n_m.values * 100,
                                       "lo": [c[0] * 100 for c in ic], "hi": [c[1] * 100 for c in ic]})
                    fig = px.line(tt, x="Mes", y="% relevante", markers=True, error_y=tt["hi"] - tt["% relevante"],
                                  error_y_minus=tt["% relevante"] - tt["lo"], color_discrete_sequence=["#EF4444"])
                    fig.update_layout(height=380, margin=dict(t=10, b=10, l=10, r=10))
                    st.plotly_chart(fig, use_container_width=True)


# ==========================================================
# 6. FILTROS (sidebar) Y NAVEGACIÓN
# ==========================================================
generos_op, modalidades_op, estudios_op = obtener_opciones_filtros()
st.sidebar.markdown("---")
st.sidebar.subheader("🎯 Filtros")
st.sidebar.caption("Aplican a «Demografía y clínica», «Inferencial» y «Explorador». Las palabras clave de la clasificación se eligen en «Demografía y clínica».")
genero_sel = st.sidebar.selectbox("Género del paciente:", generos_op)
modalidad_sel = st.sidebar.selectbox("Modalidad:", modalidades_op)
estudio_sel = st.sidebar.selectbox("Tipo de estudio:", estudios_op)

f = {
    "genero": genero_sel, "modalidad": modalidad_sel, "estudio": estudio_sel,
    "relevantes": tuple(sorted({k.strip().lower() for k in st.session_state.get("kw_rel", list(RELEVANTES_DEFECTO)) if k.strip()})),
    "normales": tuple(sorted({k.strip().lower() for k in st.session_state.get("kw_norm", list(NORMALES_DEFECTO)) if k.strip()})),
    "negacion": st.session_state.get("kw_neg", True),
}

st.sidebar.markdown("---")
st.sidebar.subheader("⚡ Caché")
_huella = huella_actual()
_n_cache = sum(1 for _ in CACHE_DIR.glob(f"{CODIGO_VERSION}_{_huella}_*.pkl")) if CACHE_DIR.exists() else 0
st.sidebar.caption(f"{_n_cache} resultados guardados en disco. Solo se recalculan si cambia la base de datos "
                   f"(huella `{_huella}`, se comprueba cada 10 s) o este archivo de código.")
if st.sidebar.button("🔄 Recalcular todo"):
    invalidar_cache()
    st.rerun()

SEC_PROC, SEC_DEMO = "⚙️ Datos y procesamiento", "👥 Demografía y clínica"
SEC_INF, SEC_EXPL = "📐 Inferencial", "🔎 Explorador"
seccion = st.radio("Sección", [SEC_PROC, SEC_DEMO, SEC_INF, SEC_EXPL], horizontal=True, label_visibility="collapsed")
st.caption("Solo se calcula la sección seleccionada, para que el dashboard responda rápido con bases grandes.")

if seccion == SEC_PROC:
    ui_proceso()
elif seccion == SEC_DEMO:
    ui_demo_clinica()
elif seccion == SEC_INF:
    ui_inferencial()

# ==========================================================
# 11. EXPLORADOR
# ==========================================================
else:
    st.header("🔎 Explorador de informes")
    st.markdown("Busca un término en los hallazgos o conclusiones extraídos y revisa los informes que coinciden "
                "(respeta los filtros del panel lateral).")
    c1, c2 = st.columns([3, 1])
    termino = c1.text_input("Término a buscar", placeholder="p. ej. nódulo, fractura, derrame")
    donde = c2.radio("Buscar en", ["Ambos", "Hallazgos", "Conclusiones"])

    if termino.strip():
        campos = {"Hallazgos": ["hallazgos"], "Conclusiones": ["conclusiones"],
                  "Ambos": ["hallazgos", "conclusiones"]}[donde]
        rx = {"$regex": re.escape(termino.strip()), "$options": "i"}
        elem = {"$or": [{c: rx} for c in campos]}
        query = {ESTUDIOS: {"$elemMatch": elem}}
        if f["estudio"] != "TODOS":  # informes que contienen ese estudio (nombre unificado) y el término buscado
            query["$expr"] = {"$in": [f["estudio"], estudios_unicos()]}
        if f["genero"] != "TODOS":
            query["sexo"] = f["genero"]
        if f["modalidad"] != "TODOS":
            query["modalidad"] = filtro_modalidad(f["modalidad"])

        try:
            n = collection.count_documents(query, limit=10000, maxTimeMS=30000)
            cursor = collection.find(query, {"sexo": 1, "modalidad": 1, "status": 1, ESTUDIOS: 1},
                                     max_time_ms=30000).limit(200)
            filas = []
            for d in cursor:
                estudios = (d.get("reporte_estructurado") or {}).get("estudios_detallados") or []
                estudios = [e for e in estudios if isinstance(e, dict)]
                filas.append({
                    "ID": str(d["_id"]), "Sexo": d.get("sexo"), "Modalidad": d.get("modalidad"),
                    "Status": d.get("status"),
                    "Estudios": " | ".join(str(e.get("estudio", "")) for e in estudios),
                    "Hallazgos": " || ".join(str(h) for e in estudios for h in e.get("hallazgos", []))[:400],
                    "Conclusiones": " || ".join(str(c) for e in estudios for c in e.get("conclusiones", []))[:400],
                })
            st.metric("Informes que coinciden", f"{n:,}" if n < 10000 else "10.000+")
            if filas:
                df_res = pd.DataFrame(filas)
                st.dataframe(df_res, use_container_width=True, hide_index=True)
                st.caption("Se muestran los primeros 200 resultados.")
                st.download_button("📥 Descargar resultados (CSV)", df_res.to_csv(index=False).encode("utf-8"),
                                   file_name="resultados_busqueda.csv", mime="text/csv")
            else:
                st.info("Sin coincidencias.")
        except PyMongoError as e:
            st.warning(f"La búsqueda tardó demasiado o falló ({e}). Prueba un término más específico o añade filtros.")
