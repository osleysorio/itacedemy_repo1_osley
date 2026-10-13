import asyncio
import html as html_lib
import logging
import math
import re
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne
from tqdm.asyncio import tqdm

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("motor").setLevel(logging.WARNING)

MONGO_URI = "mongodb://localhost:27017/"
DB_NAME = "sistema_medico_procesado"
COLECCION_NAME = "reportes_raw1"
TAMAÑO_LOTE = 2000
EDAD_MIN, EDAD_MAX = 0, 120
REPROCESAR_FALLIDOS = True  # Mantenlo en True para que vuelva a evaluar los registros corregibles

# ---------------------------------------------------------------- limpieza y regex
RE_TAG = re.compile(r"</?[a-zA-Z][^>]*>")  # elimina etiquetas HTML
UM = r"meses|mes|d[ií]as|d[ií]a|semanas|semana|[mM]"  # unidades menores o 'M' compacta
ANIOS = r"a[ñn]os?|[yY]"  # años o 'Y' compacta

# 1) Cabecera estándar: Busca "edad", permite dos puntos opcionales, espacios opcionales, el número y la unidad
RE_CABECERA = re.compile(rf"edad\s*:?\s*(\d{{1,3}})\s*({ANIOS}|{UM})?", re.I)

# 🔥 NUEVOS PATRONES: Para formatos donde la edad viene pegada al nombre o al final de una línea de texto de cabecera
# Se evalúan en orden de arriba a abajo.
PATRONES_FORMATO_NOMBRE = [
    # Captura: "GONEL-61" o "GONEL-061" (Letras, un guion, y 2 a 3 dígitos justo después)
    re.compile(r"\b[a-zA-ZÑñáéíóúÁÉÍÓÚ]+\s*-\s*(\d{2,3})\b"),
    
    # Captura: "YORELY 44" al final de una secuencia de nombres en mayúsculas. 
    # Evita IDs largos exigiendo que el número sea de exactamente 2 o 3 dígitos aislado al final del bloque de texto.
    re.compile(r"\b[A-ZÑÁÉÍÓÚ]{3,}\s+(\d{2,3})\b") 
]

# 2) Cuerpo clínico: patrones con contexto de paciente estándar
PATRONES_CUERPO = [
    re.compile(rf"(\d{{1,3}})\s*({ANIOS}|{UM})\s+de\s+(?:edad|vida)", re.I),
    re.compile(rf"(?:paciente|var[oó]n|mujer|femenin[oa]|masculin[oa]|ni[ñn][oa]|lactante|adolescente|anciano|anciana)\s+(?:de\s+)?(\d{{1,3}})\s*({ANIOS}|{UM})?", re.I),
    re.compile(rf"edad\s*(?:de|:)?\s*(\d{{1,3}})\s*({ANIOS}|{UM})?", re.I),
]

# Limpia el string directo del campo 'edad' de la base de datos (ej: "065Y")
RE_EDAD_TEXTO = re.compile(r"^\s*(\d{1,3})\s*([a-zA-ZáéíóúñÁÉÍÓÚÑ]*)\s*$", re.I)


def a_texto(valor):
    """HTML (o NaN/None) -> texto plano rápido, sin BeautifulSoup."""
    if valor is None or isinstance(valor, float):
        return ""
    s = str(valor)
    if not s.strip() or s.strip().lower() == "nan":
        return ""
    if "<" not in s and "&lt;" in s:  # HTML doble-escapado
        s = html_lib.unescape(s)
    s = html_lib.unescape(RE_TAG.sub(" ", s))
    return re.sub(r"\s+", " ", s).strip()


def _a_anios(numero, unidad=None):
    """Convierte (número, unidad) a años enteros; meses/m/días -> 0. None si está fuera de rango."""
    if unidad and not re.match(ANIOS, unidad, re.I):
        return 0
    n = int(numero)
    return n if EDAD_MIN <= n <= EDAD_MAX else None


def extraer_edad(html_cabecera, html_cuerpo):
    """Devuelve (edad | None, origen). Intenta primero por cabeceras y formatos especiales."""
    cabecera = a_texto(html_cabecera)
    
    # Intento 1: Buscar la estructura clásica "Edad: XX"
    for m in RE_CABECERA.finditer(cabecera):
        edad = _a_anios(m.group(1), m.group(2))
        if edad is not None:
            return edad, "cabecera"

    # Intento 2: Buscar formatos especiales pegados al nombre (ej: "GONEL-61" o "YORELY 44")
    for patron in PATRONES_FORMATO_NOMBRE:
        m = patron.search(cabecera)
        if m:
            edad = _a_anios(m.group(1)) # No tienen unidad (Y/M), asumimos años directamente
            if edad is not None:
                return edad, "cabecera_formato_nombre"

    # Intento 3: Buscar en el resto del cuerpo clínico si lo anterior falla
    cuerpo = a_texto(html_cuerpo)
    for patron in PATRONES_CUERPO:
        for m in patron.finditer(cuerpo):
            edad = _a_anios(m.group(1), m.group(2))
            if edad is not None:
                return edad, "cuerpo"
                
    return None, "sin_dato"


def edad_desde_valor_existente(valor):
    """Extrae la edad si ya viene como un string directo en el campo 'edad' (ej: '065Y')."""
    if isinstance(valor, str):
        m = RE_EDAD_TEXTO.match(valor)
        if m:
            return _a_anios(m.group(1), m.group(2))
    return None


def valor_original_util(valor):
    if valor is None:
        return False
    if isinstance(valor, float) and math.isnan(valor):
        return False
    return not (isinstance(valor, str) and valor.strip().lower() in ("", "nan"))


# ---------------------------------------------------------------- consulta
QUERY_INVALIDOS = {"$or": [
    {"edad": None},
    {"edad": float("nan")},
    {"edad": {"$type": "string"}},
    {"edad": {"$lt": EDAD_MIN}},
    {"edad": {"$gt": EDAD_MAX}},
]}


def construir_filtro():
    estado = {"edad_homogeneizada": {"$ne": True}} if REPROCESAR_FALLIDOS else {"edad_homogeneizada": {"$exists": False}}
    return {"$and": [QUERY_INVALIDOS, estado]}


def procesar_lote(documentos):
    """Trabajo de CPU por lote."""
    ops, stats = [], Counter()
    for doc in documentos:
        original = doc.get("edad")
        
        # 1. Intentar extraer del valor directo del campo 'edad' (ej: "065Y")
        edad = edad_desde_valor_existente(original)
        origen = "valor_existente" if edad is not None else None
        
        # 2. Si falla, buscar text (html_cabecera) usando patrones estándar y los nuevos de nombres
        if edad is None:
            edad, origen = extraer_edad(doc.get("text"), doc.get("resto_html"))

        cambios = {"edad": edad, "edad_homogeneizada": edad is not None}
        if edad is not None:
            cambios["edad_origen"] = origen
        if valor_original_util(original):
            cambios["edad_original"] = original  
            
        ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": cambios}))
        stats[origen] += 1
    return ops, stats


# ---------------------------------------------------------------- proceso principal
async def homogeneizar_edades_masivo():
    client = AsyncIOMotorClient(MONGO_URI)
    coleccion = client[DB_NAME][COLECCION_NAME]
    filtro_base = construir_filtro()

    total = await coleccion.count_documents(filtro_base)
    logging.info(f"Registros a procesar (incluyendo nuevos formatos de nombres): {total:,}")
    if total == 0:
        client.close()
        return

    stats_total, ultimo_id = Counter(), None
    with tqdm(total=total, desc="Homogeneizando", unit="doc") as pbar:
        while True:
            filtro = {"$and": filtro_base["$and"] + ([{"_id": {"$gt": ultimo_id}}] if ultimo_id is not None else [])}
            docs = await (coleccion.find(filtro, {"edad": 1, "text": 1, "resto_html": 1})
                          .sort("_id", 1).limit(TAMAÑO_LOTE).to_list(length=TAMAÑO_LOTE))
            if not docs:
                break
            ultimo_id = docs[-1]["_id"]

            ops, stats = await asyncio.to_thread(procesar_lote, docs)
            await coleccion.bulk_write(ops, ordered=False)
            stats_total.update(stats)
            pbar.update(len(docs))

    client.close()
    logging.info(f"Resultado final del procesamiento: {dict(stats_total)}")
    logging.info("🎉 Homogeneización completada con éxito.")


if __name__ == "__main__":
    asyncio.run(homogeneizar_edades_masivo())
