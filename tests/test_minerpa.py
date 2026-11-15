import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "herramientas"))

from actualizar_minerpa import aplanar, cargar_constantes, comparar, reemplazar_constante
from minerpa_pipeline import ErrorMinerpa, transformar_b, transformar_dm, validar_crudo


def tabla(headers, rows):
    return {"headers": headers, "rows": rows, "id": "1"}


class ActualizacionTests(unittest.TestCase):
    def test_reemplaza_una_constante_sin_tocar_las_otras(self):
        html = 'const D = {"x":[[2024,1]]};\nconst B = {"ok":true};\nconst DM = {"years":[2024]};\n'
        valores = cargar_constantes(html)
        self.assertEqual(valores["D"]["x"][0][1], 1)
        nuevo = reemplazar_constante(html, "D", {"x": [[2024, 2]]})
        self.assertIn('const D = {"x":[[2024,2]]};', nuevo)
        self.assertIn('const B = {"ok":true};', nuevo)

    def test_bloquea_perdida_y_cero_sustituto(self):
        c = comparar({"x": [[2023, 10], [2024, 20]]}, {"x": [[2023, 10], [2024, 0]]})
        self.assertEqual(len(c["borrados"]), 1)
        c = comparar({"x": [[2023, 10], [2024, 20]]}, {"x": [[2024, 20]]})
        self.assertEqual(len(c["borrados"]), 1)

    def test_acepta_periodo_nuevo(self):
        c = comparar({"x": [[2024, 20]]}, {"x": [[2024, 20], [2025, 21]]})
        self.assertFalse(c["borrados"])
        self.assertEqual(len(c["nuevos"]), 1)

    def test_bloquea_revision_extrema(self):
        c = comparar({"x": [[2024, 100]]}, {"x": [[2024, 220]]})
        self.assertEqual(len(c["anomalias_mayores"]), 1)

    def test_detecta_cambio_de_categoria(self):
        c = comparar({"x": {"rows": [["Categoría A", 10]]}}, {"x": {"rows": [["Categoría B", 10]]}})
        self.assertEqual(len(c["cambios_estructura"]), 1)

    def test_catalogo_incompleto_falla(self):
        with self.assertRaises(ErrorMinerpa):
            validar_crudo({})

    def test_atipico_nuevo_falla(self):
        with tempfile.TemporaryDirectory() as td:
            base, candidato = Path(td) / "base.json", Path(td) / "candidato.json"
            base.write_text(json.dumps({"atipicos": [{"donde": "x", "msg": "conocido"}]}), encoding="utf-8")
            candidato.write_text(json.dumps({"atipicos": [{"donde": "x", "msg": "conocido"}, {"donde": "y", "msg": "nuevo"}]}), encoding="utf-8")
            r = subprocess.run([sys.executable, str(ROOT / "herramientas" / "bloquear_atipicos_nuevos.py"), str(base), str(candidato)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 1)
            self.assertIn("Anomalía nueva", r.stdout)


class TransformacionTests(unittest.TestCase):
    def test_transforma_pib_por_demanda(self):
        nombres_real = [
            "Gasto de consumo final del Gobierno General", "Gasto de consumo final de los hogares e ISFLSH",
            "Formación bruta de capital fijo", "Variación de las existencias", "Exportaciones de bienes y servicios",
            "Menos:  Importaciones de bienes y servicios", "PRODUCTO INTERNO BRUTO A PRECIOS DE COMPRADOR",
        ]
        nombres_nom = nombres_real.copy()
        nombres_nom[1] = "Gasto de consumo final privado"
        nombres_nom[5] = "Importaciones de bienes y servicios"
        def pagina(nombres):
            rows = [[i + 1, n, str(10 + i), str(20 + i)] for i, n in enumerate(nombres)]
            return {"tables": [tabla(["wdt_ID", "Enfoque del gasto", "2023", "2024"], rows), tabla(["wdt_ID", "Enfoque del gasto", "2023", "2024"], rows * 5)]}
        crudo = {
            "producto-interno-bruto-real-segun-enfoque-de-demanda-base-2018": pagina(nombres_real),
            "producto-interno-bruto-nominal-segun-enfoque-de-demanda-base-2018": pagina(nombres_nom),
        }
        dm = transformar_dm(crudo)
        self.assertEqual(dm["years"], [2023, 2024])
        self.assertEqual(dm["real"]["priv"], [11.0, 21.0])
        self.assertEqual(dm["nom"]["m"], [15.0, 25.0])

    def test_transforma_banca(self):
        def wide(slug, labels):
            return {slug: {"tables": [tabla(["wdt_ID", "Actividad", "2024", "2025"], [[i + 1, n, "1", "2"] for i, n in enumerate(labels)])]}}
        crudo = {}
        crudo.update(wide("saldo-de-la-cartera-de-credito-segun-destino", ["Cartera Crediticia Total", "Cartera Interna", "Cartera Externa"]))
        crudo.update(wide("saldo-de-la-cartera-de-credito-segun-sector-economico", ["Créditos Internos", "Sector Público", "Sector Privado", "Instituciones Financieras", "Agricultura", "Ganadería", "Pesca", "Minería", "Comercio", "Industria", "Hipotecas", "Construcción", "Micro préstamos", "Consumo personal"]))
        crudo.update(wide("saldo-de-los-depositos", ["DEPÓSITOS", "Depósitos Internos", "Depósitos Internos Oficiales", "Depósitos Internos de Particulares", "Depósitos Internos de Particulares A la Vista", "Depósitos Internos de Particulares A Plazo", "Depósitos Internos de Particulares De Ahorros", "Depósitos Internos De Bancos", "Depósitos Internos De Bancos A la Vista", "Depósitos Internos De Bancos A Plazo", "Depósitos Externos", "Depósitos Externos Oficiales", "Depósitos Externos De Particulares", "Depósitos Externos De Particulares A la Vista", "Depósitos Externos De Particulares A Plazo", "Depósitos Externos De Particulares De Ahorros", "Depósitos Externos De Bancos"]))
        crudo.update(wide("tasa-de-interes-sobre-credito", ["Tasa sobre créditos al por mayor", "Tasa sobre créditos al por menor", "Tasa sobre tarjetas de crédito", "Tasa sobre préstamos personales", "Tasa sobre autos", "Tasa sobre vivienda (no preferencial)", "Tasa sobre agricultura", "Tasa sobre ganadería", "Tasa sobre construcción", "Tasa sobre industria"]))
        crudo.update(wide("tasa-de-interes-sobre-depositos", ["Tasa sobre depósitos a plazo fijo 1 mes", "Tasa sobre depósitos a plazo fijo 3 meses", "Tasa sobre depósitos a plazo fijo 6 meses", "Tasa sobre depósitos a plazo fijo 1 Año", "Tasa sobre depósitos de ahorro corriente"]))
        inflacion = ["TOTAL", "Alimentos y Bebidas no Alcohólicas", "Bebidas Alcohólicas y Tabaco", "Prendas de Vestir y Calzado", "Vivienda, Agua, Electricidad y Gas", "Muebles, Artículos para el Hogar", "Salud", "Transporte", "Comunicaciones", "Recreación y Cultura", "Educación", "Restaurantes y Hoteles", "Bienes y Servicios Diversos"]
        crudo["tasa-de-inflacion-por-grupos"] = {"tables": [tabla(["wdt_ID", "Actividad", "2013"], [[1, "TOTAL", "1"]] * 10), tabla(["wdt_ID", "Actividad", "2024", "2025"], [[i + 1, n, "1", "2"] for i, n in enumerate(inflacion)])]}
        b = transformar_b(crudo)
        self.assertEqual(b["cred"]["years"], [2024, 2025])
        self.assertEqual(b["cred"]["tot"], [1.0, 2.0])
        self.assertIsNone(b["tcred"]["vivienda"][0] if b["tcred"]["vivienda"][0] == 0 else None)
        self.assertEqual(len(b["infg"]["rows"]), 10)


if __name__ == "__main__":
    unittest.main()
