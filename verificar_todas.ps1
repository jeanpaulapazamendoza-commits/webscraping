# Verificador masivo de URLs — 75 URLs totales
#
# Uso:    .\verificar_todas.ps1
#
# Si PowerShell se queja de "no se pueden ejecutar scripts", correlo así una vez:
#     Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
#
# Verifica las 75 URLs en ~60-90 segundos y muestra el estado de cada una.

$urls = @(
  # ── TOTTUS (22) ──────────────────────────────────────────────
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16918/Carne-de-Res",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16919/Carne-de-Pollo",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16920/Carne-de-Cerdo",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16921/Carne-de-Pavo",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16923/Arma-Tu-Parrilla",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16994/Limones--Naranjas-y-Mandarinas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16993/Fresas-y-Arandanos",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16992/Granadillas--Tunas-y-Pitahayas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16991/Paltas-y-Frutas-Nativas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16986/Platanos--Papayas--Pinas-y-Tropicales",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16987/Manzanas--Peras--Uvas-y-Membrillos",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16995/Duraznos--Sandias--Melones-y-Mangos",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16989/Zapallos--Berenjenas-y-Caihuas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16979/Arvejas--Vainitas-y-Habas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16980/Choclo--Zanahoria--Pepinos-y-Beterragas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16978/Cebolla--Tomate--Ajo-y-Ajies",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16977/Papas-y-Camotes",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16985/Lechugas--Espinacas-y-Hojas",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16981/Apio--Esparragos-y-Pimientos",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16982/Brocoli--Col--Coliflor-y-Alcachofa",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG10160/Organicos-y-Verduras-Baby",
  "https://www.tottus.com.pe/tottus-pe/lista/CATG16996/Jugos-Naturales",

  # ── WONG (7) ──────────────────────────────────────────────────
  "https://www.wong.pe/carnes-aves-y-pescados/res-y-otras-carnes",
  "https://www.wong.pe/carnes-aves-y-pescados/res-y-otras-carnes/cortes-premium",
  "https://www.wong.pe/carnes-aves-y-pescados/aves-y-huevos",
  "https://www.wong.pe/carnes-aves-y-pescados/cerdo",
  "https://www.wong.pe/carnes-aves-y-pescados/hamburguesas-y-apanados",
  "https://www.wong.pe/frutas-y-verduras/frutas",
  "https://www.wong.pe/frutas-y-verduras/verduras",

  # ── METRO (8) ─────────────────────────────────────────────────
  "https://www.metro.pe/carnes-aves-y-pescados/res-y-otras-carnes",
  "https://www.metro.pe/carnes-aves-y-pescados/res-y-otras-carnes/cortes-premium",
  "https://www.metro.pe/carnes-aves-y-pescados/aves-y-huevos",
  "https://www.metro.pe/carnes-aves-y-pescados/cerdo",
  "https://www.metro.pe/carnes-aves-y-pescados/hamburguesas-y-apanados",
  "https://www.metro.pe/carnes-aves-y-pescados/pescados-y-mariscos",
  "https://www.metro.pe/frutas-y-verduras/frutas",
  "https://www.metro.pe/frutas-y-verduras/verduras",

  # ── PLAZA VEA (34) ────────────────────────────────────────────
  # carnes (11)
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/pollo/pollo-entero",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/pollo/elaborados-de-pollo",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/pollo/menudencias-de-pollo",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/res/carne-molida-de-res",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/res/cortes-parrilleros-de-res",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/res/cortes-caseros-de-res",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/res/menudencias-de-res",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/res/elaborados-de-res",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/cerdo/cortes-parrilleros-de-cerdo",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/cerdo/cortes-caseros-de-cerdo",
  "https://www.plazavea.com.pe/carnes-aves-y-pescados/cerdo/elaborados-de-cerdo",
  # frutas (13)
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/naranja-mandarina-y-otros-citricos",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/manzana-pera-y-membrillo",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/papaya-y-pina",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/paltas",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/platano-y-uva",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/fresa-arandano-aguaymanto-y-otros-berries",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/melocoton-durazno-y-mango",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/maracuya-granadilla-tuna-y-tumbo",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/sandia-y-melon",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/chirimoya-lucuma-carambola-y-otras",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/frutas-picadas-y-preparadas",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/jugos-naturales",
  "https://www.plazavea.com.pe/frutas-y-verduras/frutas/frutas-congeladas",
  # verduras (15)
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/papa-camote-yuca-y-otros-tuberculos",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/lechuga-espinaca-y-hojas-verdes",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/cebolla-ajo-rocoto-y-aji",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/hongos-setas-y-germinados",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/tomate-pepino-y-pimiento",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/limon",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/zapallo-berenjena-y-caigua",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/zanahoria-beterraga-rabanito-y-otras-raices",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/brocoli-coliflor-alcachofa-y-col",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/apio-esparrago-y-otros-tallos",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/choclo",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/arveja-vainita-haba-y-otras-legumbres",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/hierbas-e-infusiones",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/verduras-orientales",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/ensaladas-y-verduras-picadas",
  "https://www.plazavea.com.pe/frutas-y-verduras/verduras/verduras-congeladas"
)

Write-Host "Verificando $($urls.Count) URLs..." -ForegroundColor Cyan
python verificar_urls.py @urls

## version v2
