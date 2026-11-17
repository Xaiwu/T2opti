"""Tarea Computacional 3: ruteo consistente multidia, Gurobi vs. CPLEX.

Resuelve las instancias de data/instancias/ con el mismo modelo en Gurobi y en
CPLEX (docplex), con parametros por defecto (solo TimeLimit), compara
relajacion continua y modelo entero, y guarda logs de ambos solvers.

Uso:  uv run main.py [--limite 600] [--instancias I1 I2] [--sin-start] [--regenerar]
Requiere gurobipy con licencia academica y cplex/docplex con la edicion completa
(docplex config --upgrade <carpeta de CPLEX Studio>).
"""
import argparse
import math
import random
from pathlib import Path

import pandas as pd
from docplex.mp.model import Model

PATH_SOLOMON = Path("data/originales")
PATH_GENERADAS = Path("data/instancias")
PATH_RESULTADOS = Path("results")
PATH_LOGS = Path("logs")

# nombre -> parametros de generar() (solo se usan si falta el archivo o con --regenerar)
INSTANCIAS_CFG = {
    "I1": dict(source="R108.txt", n=20, days=3, vehicles=3, seed=42, slack=0.05),
    "I2": dict(source="R110.txt", n=30, days=3, vehicles=3, seed=43, slack=0.05),
}

instancias = {}  # se llena en main()


# ======================================================================
# 1. Lectura y generacion de instancias
# ======================================================================
def leer_solomon(path):
    """Lee cabecera VEHICLE y filas CUSTOMER, sin depender de espacios exactos."""
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    vehicles = capacity = None
    rows = {}
    in_customers = False
    for line in lines:
        s = line.split()
        if not s:
            continue
        if s[0].upper() == "CUSTOMER":
            in_customers = True
            continue
        if not in_customers and len(s) == 2:
            try:
                vehicles, capacity = int(s[0]), float(s[1])
            except ValueError:
                pass
        if in_customers and len(s) == 7:
            try:
                row = tuple(float(x) for x in s)
            except ValueError:
                continue
            i = int(row[0])
            if i != row[0] or i in rows:
                raise ValueError("Identificador no entero o duplicado")
            if row[3] < 0 or row[4] > row[5] or row[6] < 0:
                raise ValueError(f"Datos invalidos en cliente {i}")
            rows[i] = row
    if 0 not in rows or vehicles is None or capacity is None:
        raise ValueError(f"Formato Solomon incompleto: {path}")
    return rows, vehicles, capacity


def distancia(rows, i, j):
    return math.hypot(
        rows[i][1] - rows[j][1],
        rows[i][2] - rows[j][2],
    )


# ======================================================================
# 2. Generacion de instancias propias
# ======================================================================
def generar(source, destination, n, days, vehicles, seed, slack):
    rows, original_vehicles, original_q = leer_solomon(source)
    ids = sorted(i for i in rows if i != 0)[:n]  # Solo los primeros n clientes, sin el depot (0).
    # Validaciones de parametros
    if len(ids) != n or n < 15 or days < 3 or not 3 <= vehicles <= n:
        raise ValueError("Se requieren >=15 clientes, >=3 dias y 3<=vehiculos<=clientes")
    if not 0 <= slack <= 1:
        raise ValueError("El margen de capacidad debe estar entre 0 y 1")

    # Generacion de demanda diaria aleatoria, con al menos un dia activo por cliente.
    rng = random.Random(seed)
    demand = {}
    for i in ids:
        active = set(rng.sample(range(days), rng.choice([days - 1, days])))
        demand[i] = [max(1, round(rows[i][3] * rng.uniform(0.8, 1.2)))
        if t in active else 0 for t in range(days)]

    # Agrupacion geografica sencilla para construir UNA solución factible de referencia.
    ordered = sorted(
        ids, key=lambda i: math.atan2(
            rows[i][2] - rows[0][2],
            rows[i][1] - rows[0][1],
        ),
    )

    # Equilibrio de numero de clientes
    groups = [
        ordered[r * n // vehicles:(r + 1) * n // vehicles]
        for r in range(vehicles)
    ]

    # Calculo de cargas diarias para cada grupo.
    loads = [
        [sum(demand[i][t] for i in group) for t in range(days)]
        for group in groups
    ]
    # Toma la mayor carga y se redondea hacia arriba con el margen de slack para obtener la nueva capacidad Q.
    q = math.ceil(max(max(x) for x in loads) * (1 + slack))

    # Preparar horarios de apertura y cierre, y ventanas de servicio para cada cliente.
    opening, original_close = rows[0][4:6]
    service = {i: max(1.0, rows[i][6]) for i in ids}
    ready = {i: rows[i][4] for i in ids}
    due = {i: rows[i][5] for i in ids}

    schedules = []
    closing = original_close

    # Construir los recorridos diarios
    for r, group in enumerate(groups, 1):
        for t in range(days):
            # Recorre cada ruta y cada día. Las rutas se numeran desde 1.
            remaining = {i for i in group if demand[i][t] > 0}
            previous, clock, visits = 0, opening, []
            while remaining:
                # Escoge el siguiente cliente que podria empezar antes.
                # inicio posible = max{apertura del cliente, hora actual + tiempo de viaje}, si hay empate el
                # identificador mas bajo gana.
                i = min(
                    remaining, key=lambda j: (
                        max(ready[j], clock + distancia(rows, previous, j)), j,
                    ),
                )
                # Calcula el inicio de servicio y actualiza la ventana de cierre si es necesario.
                start = max(ready[i], clock + distancia(rows, previous, i))
                due[i] = max(due[i], math.ceil(start))

                # Registra la visita, suma el servicio, actualiza la ubicación y elimina al cliente de los pendientes.
                visits.append((i, start))
                clock, previous = start + service[i], i
                remaining.remove(i)

            # Volver al deposito, calcula el regreso y amplía el cierre del depósito si hace falta.
            return_time = clock + distancia(rows, previous, 0)
            closing = max(closing, math.ceil(return_time))
            schedules.append((r, t, visits, return_time))

    # La siguiente parte recorre los horarios construidos y los comprueba.
    # Validacion independiente de la solución factible de referencia: cobertura, consistencia, carga y tiempos.
    for t in range(days):
        seen = []
        for r, day, visits, return_time in schedules:
            if day != t:
                continue
            previous, clock, load = 0, opening, 0
            for i, start in visits:
                if i not in groups[r - 1] or demand[i][t] <= 0:
                    raise ValueError("solución factible de referencia inconsistente")
                if not ready[i] <= start <= due[i]:
                    raise ValueError("Ventana incumplida")
                if start + 1e-8 < clock + distancia(rows, previous, i):
                    raise ValueError("Tiempo de viaje incumplido")
                seen.append(i)
                load += demand[i][t]
                previous, clock = i, start + service[i]
            if load > q or return_time > closing:
                raise ValueError("Capacidad u horario incumplido")
            if abs(return_time - clock - distancia(rows, previous, 0)) > 1e-8:
                raise ValueError("Retorno inconsistente")
        expected = sorted(i for i in ids if demand[i][t] > 0)
        if sorted(seen) != expected:
            raise ValueError("Cobertura diaria incorrecta")

    destination.parent.mkdir(parents=True, exist_ok=True)
    name = destination.stem
    text = [
        f"NAME {name}", "FORMAT CONVRP_MULTIDAY_V1",
        f"SOURCE {source.name}",
        f"SEED {seed}", f"DAYS {days}", f"VEHICLES {vehicles}",
        f"CAPACITY {q}", "DISTANCE EUCLIDEAN_DOUBLE",
        "TRAVEL_TIME EQUAL_DISTANCE", "TIME_UNIT ABSTRACT",
        "", "DEPOT", "ID X Y OPEN CLOSE",
        f"0 {rows[0][1]:g} {rows[0][2]:g} {opening:g} {closing:g}",
        "", "CUSTOMERS", "ID X Y READY DUE SERVICE",
    ]
    for i in ids:
        text.append(
            f"{i} {rows[i][1]:g} {rows[i][2]:g} "
            f"{ready[i]:g} {due[i]:g} {service[i]:g}",
        )
    text += ["", "DAILY_DEMAND", "ID " + " ".join(f"DAY{t + 1}" for t in range(days))]
    text += [f"{i} " + " ".join(map(str, demand[i])) for i in ids]
    text += ["", "EOF"]
    destination.write_text("\n".join(text) + "\n", encoding="utf-8")

    widened = [i for i in ids if due[i] != rows[i][5]]
    report = [
        f"Instancia: {name}", "solución factible de referencia VERIFICADO (no es una solucion optima)",
        f"Dificultad computacional: ver results/{name}_configuraciones.csv",
        f"Vehiculos originales: {original_vehicles}; nuevos: {vehicles}",
        f"Capacidad original: {original_q:g}; nueva: {q}; margen: {slack:.1%}",
        f"Cierre original: {original_close:g}; nuevo: {closing:g}",
        f"Ventanas con limite superior ampliado: {len(widened)}", "",
    ]
    for i in widened:
        report.append(f"Cliente {i}: DUE {rows[i][5]:g} -> {due[i]:g}")
    for r, group in enumerate(groups, 1):
        report += [f"\nAsignacion solución factible de referencia ruta {r}: {group}", f"Cargas: {loads[r - 1]}"]
    for r, t, visits, return_time in schedules:
        route = " -> ".join(["0"] + [str(i) for i, _ in visits] + ["0"])
        starts = ", ".join(f"{i}: {w:.6f}" for i, w in visits)
        report += [
            f"\nRuta {r}, dia {t + 1}: {route}",
            f"Salida fija: {opening:g}; retorno: {return_time:.6f}",
            f"Inicio de servicio (valores mostrados redondeados): {starts}",
        ]
    destination.with_name(name + "_verificacion.txt").write_text(
        "\n".join(report) + "\n", encoding="utf-8",
    )
    print(
        f"{destination.name}: {n} clientes, {days} dias, {vehicles} vehiculos, "
        f"Q={q}, cierre={closing:g}, ventanas ampliadas={len(widened)}; solución factible de referencia OK",
    )


# Lector de instancias propias
def leer_multidia(path):
    """Lee el formato CONVRP_MULTIDAY_V1 generado por nuestro código."""
    path = Path(path)

    cabecera = {}
    clientes = {}
    demandas = {}
    deposito = None
    seccion = None

    for numero_linea, linea in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1,
    ):
        linea = linea.strip()

        if not linea or linea.startswith("#"):
            continue

        if linea == "EOF":
            break

        if linea in {"DEPOT", "CUSTOMERS", "DAILY_DEMAND"}:
            seccion = linea
            continue

        partes = linea.split()

        # Ignorar títulos de columnas.
        if partes[0] == "ID":
            continue

        try:
            if seccion is None:
                clave, valor = linea.split(maxsplit=1)
                cabecera[clave] = valor

            elif seccion == "DEPOT":
                if len(partes) != 5 or deposito is not None:
                    raise ValueError("Debe existir una única fila de depósito")

                identificador = int(partes[0])
                x, y, apertura, cierre = map(float, partes[1:])

                deposito = {
                    "id": identificador,
                    "x": x,
                    "y": y,
                    "apertura": apertura,
                    "cierre": cierre,
                }

            elif seccion == "CUSTOMERS":
                if len(partes) != 6:
                    raise ValueError("Fila de cliente con columnas incorrectas")

                i = int(partes[0])

                if i in clientes:
                    raise ValueError(f"Cliente duplicado: {i}")

                x, y, inicio, fin, servicio = map(float, partes[1:])

                clientes[i] = {
                    "x": x,
                    "y": y,
                    "inicio": inicio,
                    "fin": fin,
                    "servicio": servicio,
                }

            elif seccion == "DAILY_DEMAND":
                i = int(partes[0])

                if i in demandas:
                    raise ValueError(f"Demanda duplicada para cliente {i}")

                demandas[i] = list(map(float, partes[1:]))

        except ValueError as error:
            raise ValueError(
                f"{path.name}, línea {numero_linea}: {error}",
            ) from error

    # Comprobaciones de estructura.
    if cabecera.get("FORMAT") != "CONVRP_MULTIDAY_V1":
        raise ValueError("Formato multidía no reconocido")

    dias = int(cabecera["DAYS"])
    vehiculos = int(cabecera["VEHICLES"])
    capacidad = float(cabecera["CAPACITY"])

    if dias <= 0 or vehiculos <= 0 or capacidad <= 0:
        raise ValueError("Días, vehículos y capacidad deben ser positivos")

    if deposito is None or deposito["id"] != 0:
        raise ValueError("Falta el depósito con identificador 0")

    if not 0 <= deposito["apertura"] <= deposito["cierre"]:
        raise ValueError("Horario del depósito inválido")

    n = len(clientes)

    # Nuestros archivos generados seleccionan los clientes 1,...,n.
    if n == 0 or set(clientes) != set(range(1, n + 1)):
        raise ValueError("Se esperan clientes numerados desde 1 hasta n")

    if set(demandas) != set(clientes):
        raise ValueError("Los clientes y las filas de demanda no coinciden")

    for i in clientes:
        c = clientes[i]

        if not 0 <= c["inicio"] <= c["fin"]:
            raise ValueError(f"Ventana inválida del cliente {i}")

        if c["servicio"] <= 0:
            raise ValueError(f"Servicio no positivo del cliente {i}")

        if len(demandas[i]) != dias:
            raise ValueError(f"Cantidad de días incorrecta del cliente {i}")

        if any(q < 0 for q in demandas[i]):
            raise ValueError(f"Demanda negativa del cliente {i}")

    if (
        cabecera.get("DISTANCE") != "EUCLIDEAN_DOUBLE"
        or cabecera.get("TRAVEL_TIME") != "EQUAL_DISTANCE"
    ):
        raise ValueError("Convención de distancia o tiempo no compatible")

    # Conjuntos del modelo.
    C = list(range(1, n + 1))
    D = list(range(1, dias + 1))
    R = list(range(1, vehiculos + 1))

    retorno = n + 1
    N = [0] + C + [retorno]

    # Todos los clientes tienen ventana en nuestro formato actual.
    N_TW = C.copy()

    # Depósito de salida y retorno: misma ubicación física.
    coordenadas = {
        0: (deposito["x"], deposito["y"]),
        retorno: (deposito["x"], deposito["y"]),
    }
    coordenadas.update(
        {
            i: (clientes[i]["x"], clientes[i]["y"]) for i in C
        },
    )

    td = {
        (i, j): math.hypot(
            coordenadas[i][0] - coordenadas[j][0],
            coordenadas[i][1] - coordenadas[j][1],
        )
        for i in N
        for j in N
    }

    tt = td.copy()

    q = {
        (i, t): demandas[i][t - 1]
        for i in C
        for t in D
    }

    O = [(i, t) for i in C for t in D if q[i, t] > 0]

    st = {
        (i, t): clientes[i]["servicio"]
        for i, t in O
    }

    # Arcos diarios: solo clientes con pedido ese día.
    A = []

    for t in D:
        activos = [i for i in C if q[i, t] > 0]

        A.append((0, retorno, t))

        A.extend((0, i, t) for i in activos)
        A.extend((i, retorno, t) for i in activos)

        A.extend(
            (i, j, t)
                for i in activos
                for j in activos
                if i != j,
        )

    return {
        "nombre": cabecera.get("NAME", path.stem),
        "C": C,
        "D": D,
        "R": R,
        "N": N,
        "N_TW": N_TW,
        "O": O,
        "A": A,
        "retorno": retorno,
        "coordenadas": coordenadas,
        "td": td,
        "tt": tt,
        "q": q,
        "st": st,
        "a": {i: clientes[i]["inicio"] for i in C},
        "b": {i: clientes[i]["fin"] for i in C},
        "Q": capacidad,
        "aW": deposito["apertura"],
        "bW": deposito["cierre"],
    }


# ======================================================================
# 3. Modelo en Gurobi, MIP start y validador
# ======================================================================
# ============================================================
# Seccion 4.1: construccion del modelo MIP con Gurobi
# ============================================================
import re

import gurobipy as gp
from gurobipy import GRB


def construir_modelo(instancia, verbo=False):
    """Modelo PLEM reducido. Devuelve (modelo, variables)."""
    C, D, R = instancia["C"], instancia["D"], instancia["R"]
    ret, A, O = instancia["retorno"], instancia["A"], set(instancia["O"])
    NTW = set(instancia["N_TW"])
    q, st, td, tt = (instancia[k] for k in ("q", "st", "td", "tt"))
    a, b, aW, bW, Q = (instancia[k] for k in ("a", "b", "aW", "bW", "Q"))

    # Cotas de W_it: lo[i] <= W_it <= up[i]. Se usan en los Big-M por arco.
    lo = {i: a[i] if i in NTW else aW for i in C}
    up = {i: b[i] if i in NTW else bW for i in C}

    m = gp.Model(f"ConVRP_{instancia['nombre']}")
    m.Params.OutputFlag = 1 if verbo else 0

    # --- Variables ---
    x = {(i, j, t, r): m.addVar(vtype=GRB.BINARY, name=f"x_{i}_{j}_d{t}_r{r}")
        for (i, j, t) in A for r in R}  # (16)
    z = {(i, r): m.addVar(vtype=GRB.BINARY, name=f"z_{i}_r{r}")
        for i in C for r in R}  # (16)
    s = {(i, t): m.addVar(lb=lo[i], ub=up[i], name=f"s_{i}_d{t}")
        for (i, t) in O}  # W_it: (16) y ventanas (8)
    # dW <= bW y rW >= aW no cortan soluciones y permiten Big-M ajustados.
    dW = {r: m.addVar(lb=aW, ub=bW, name=f"dW_r{r}") for r in R}  # (16) y (11)
    rW = {(t, r): m.addVar(lb=aW, ub=bW, name=f"rW_d{t}_r{r}")
        for t in D for r in R}  # (16) y (12)

    # --- (1) Objetivo: distancia total ---
    m.setObjective(
        gp.quicksum(td[i, j] * x[i, j, t, r] for (i, j, t) in A for r in R),
        GRB.MINIMIZE,
    )

    entra, sale = {}, {}
    for (i, j, t) in A:
        sale.setdefault((i, t), []).append(j)
        entra.setdefault((j, t), []).append(i)

    # --- (2) Cada cliente a una unica ruta ---
    for i in C:
        m.addConstr(gp.quicksum(z[i, r] for r in R) == 1, name=f"asig_{i}")

    # --- (3) y (4) Entrada y salida de cada cliente con pedido ---
    for (i, t) in O:
        for r in R:
            m.addConstr(
                gp.quicksum(x[k, i, t, r] for k in entra[i, t]) == z[i, r],
                name=f"entrada_{i}_d{t}_r{r}",
            )
            m.addConstr(
                gp.quicksum(x[i, k, t, r] for k in sale[i, t]) == z[i, r],
                name=f"salida_{i}_d{t}_r{r}",
            )

    # --- (5) Una salida del deposito por ruta y dia ---
    for t in D:
        for r in R:
            m.addConstr(
                gp.quicksum(x[0, j, t, r] for j in sale[0, t]) == 1,
                name=f"deposito_d{t}_r{r}",
            )

    # --- (7), (9), (10) Coordinacion temporal con Big-M por arco ---
    for (i, j, t) in A:
        if i == 0 and j == ret:
            continue  # ruta sin entregas
        if i == 0:  # (9) salida -> primer cliente
            M = bW + tt[0, j] - lo[j]
            for r in R:
                m.addConstr(
                    dW[r] + tt[0, j] <= s[j, t] + M * (1 - x[0, j, t, r]),
                    name=f"sal_dep_{j}_d{t}_r{r}",
                )
        elif j == ret:  # (10) ultimo cliente -> retorno
            M = up[i] + st[i, t] + tt[i, ret] - aW
            for r in R:
                m.addConstr(
                    s[i, t] + st[i, t] + tt[i, ret]
                    <= rW[t, r] + M * (1 - x[i, ret, t, r]),
                    name=f"ret_dep_{i}_d{t}_r{r}",
                )
        else:  # (7) cliente -> cliente
            M = up[i] + st[i, t] + tt[i, j] - lo[j]
            for r in R:
                m.addConstr(
                    s[i, t] + st[i, t] + tt[i, j]
                    <= s[j, t] + M * (1 - x[i, j, t, r]),
                    name=f"sec_{i}_{j}_d{t}_r{r}",
                )

    # --- (14) Capacidad por ruta y dia (Q solo aparece como RHS) ---
    for t in D:
        for r in R:
            m.addConstr(
                gp.quicksum(q[i, t] * z[i, r] for i in C if q[i, t] > 0) <= Q,
                name=f"cap_d{t}_r{r}",
            )

    return m, {"x": x, "z": z, "s": s, "dW": dW, "rW": rW}


# ============================================================
# Seccion 4.2: MIP start desde la solucion de referencia (verificacion)
# ============================================================
def solucion_de_verificacion(path):
    """Extrae rutas y inicios de servicio del archivo *_verificacion.txt."""
    lineas = Path(path).read_text(encoding="utf-8").splitlines()
    rutas, inicios = {}, {}
    patron_ruta = re.compile(r"^Ruta (\d+), dia (\d+): (.+)$")
    patron_inicio = re.compile(r"^Inicio de servicio .*\): (.*)$")
    for linea in lineas:
        linea = linea.strip()
        mr = patron_ruta.match(linea)
        if mr:
            r, t = int(mr.group(1)), int(mr.group(2))
            nodos = [int(x) for x in mr.group(3).split("->")]
            rutas[r, t] = [i for i in nodos[1:-1]]  # sin el depot
            continue
        mi = patron_inicio.match(linea)
        if mi and rutas:
            r, t = list(rutas)[-1]  # ultima ruta leida
            for par in mi.group(1).split(","):
                i, val = par.split(":")
                inicios[int(i), t] = float(val)
    return rutas, inicios


def aplicar_inicio(vars_, instancia, rutas):
    """MIP start desde la solucion de referencia."""
    C, D, R = instancia["C"], instancia["D"], instancia["R"]
    ret = instancia["retorno"]
    st, tt, a, aW = instancia["st"], instancia["tt"], instancia["a"], instancia["aW"]
    x, z, s, dW, rW = (vars_[k] for k in ("x", "z", "s", "dW", "rW"))

    pertenece = {}
    for (r, t), clientes in rutas.items():
        for i in clientes:
            pertenece.setdefault(i, r)

    for clave in x:
        x[clave].Start = 0
    for r in R:
        dW[r].Start = aW
    for t in D:
        for r in R:
            ruta = rutas.get((r, t), [])
            for p, j in zip([0] + ruta, ruta + [ret]):
                x[p, j, t, r].Start = 1
            reloj, anterior = aW, 0
            for i in ruta:
                inicio = max(a[i], reloj + tt[anterior, i])
                s[i, t].Start = inicio
                reloj, anterior = inicio + st[i, t], i
            rW[t, r].Start = reloj + tt[anterior, ret]
    for i in C:
        for r in R:
            z[i, r].Start = 1 if pertenece[i] == r else 0


# ============================================================
# Seccion 4.3: validacion independiente de la solucion del solver
# ============================================================
def validar_solucion(instancia, vars_):
    """Revisa cobertura, capacidad, ventanas, tiempos, retorno y consistencia.
    Devuelve (rutas, distancia_total). Lanza AssertionError si algo falla."""
    C, D, R = instancia["C"], instancia["D"], instancia["R"]
    ret, A, O = instancia["retorno"], instancia["A"], set(instancia["O"])
    q, st, tt = instancia["q"], instancia["st"], instancia["tt"]
    a, b, aW, bW, Q = (instancia[k] for k in ("a", "b", "aW", "bW", "Q"))
    x, s = vars_["x"], vars_["s"]
    dW, rW = vars_["dW"], vars_["rW"]

    # Con Big-M, x puede desviarse hasta IntFeasTol (1e-5) de 0/1, y esa desviacion
    # se multiplica por M en (7), (9) y (10). Tolerancia para los chequeos de tiempo.
    tol_t = 1e-5 * (bW + max(st.values()) + max(tt.values()))

    vehiculo_de, rutas = {}, {}
    for t in D:
        act = {i for i in C if (i, t) in O}
        atendidos = set()
        for r in R:
            arcos = [(i, j) for (i, j, dd) in A if dd == t and x[i, j, dd, r].X > 0.5]
            succ = dict(arcos)
            if not succ:
                continue
            ruta, nodo = [], 0
            while succ.get(nodo, ret) != ret:
                nodo = succ[nodo]
                assert nodo in act and nodo not in ruta, "subruta o nodo repetido"
                ruta.append(nodo)
            rutas[r, t] = ruta
            carga = sum(q[i, t] for i in ruta)
            assert carga <= Q, f"capacidad dia {t} vehiculo {r}: {carga} > {Q}"
            reloj, anterior = dW[r].X, 0
            for i in ruta:
                inicio = s[i, t].X
                assert a[i] - 1e-6 <= inicio <= b[i] + 1e-6, \
                    f"ventana cliente {i} dia {t}"
                assert inicio >= reloj + tt[anterior, i] - tol_t, \
                    f"tiempo de viaje violado en {i} dia {t}"
                reloj = inicio + st[i, t]
                anterior = i
                atendidos.add(i)
                # consistencia: mismo vehiculo en todos los dias
                if i in vehiculo_de:
                    assert vehiculo_de[i] == r, f"vehiculo inconsistente en {i}"
                else:
                    vehiculo_de[i] = r
            retorno = reloj + tt[anterior, ret]
            assert retorno <= rW[t, r].X + tol_t, f"rW menor al retorno real dia {t} vehiculo {r}"
            assert retorno <= bW + tol_t, f"retorno tardio dia {t} vehiculo {r}: {retorno}"
        assert atendidos == act, f"cobertura incorrecta dia {t}: {atendidos ^ act}"

    dist = sum(
        instancia["td"][i, j] * x[i, j, t, r].X
            for (i, j, t) in instancia["A"] for r in R,
    )
    return rutas, dist


ESTADOS = {
    GRB.OPTIMAL: "OPTIMO", GRB.TIME_LIMIT: "TIME_LIMIT",
    GRB.INFEASIBLE: "INFACTIBLE", GRB.INTERRUPTED: "INTERRUMPIDO",
}


def cargar_start(nombre, vars_):
    archivo = PATH_GENERADAS / f"{nombre}_verificacion.txt"
    if archivo.is_file():
        rutas_ref, _ = solucion_de_verificacion(archivo)
        aplicar_inicio(vars_, instancias[nombre], rutas_ref)


# ======================================================================
# 4. Modelo en CPLEX (docplex): mismo modelo que construir_modelo
# ======================================================================
def construir_modelo_cplex(instancia, relajado=False):
    """relajado=True: x, z continuas en [0,1] (relajacion continua)."""
    C, D, R = instancia["C"], instancia["D"], instancia["R"]
    ret, A, O = instancia["retorno"], instancia["A"], instancia["O"]
    NTW = set(instancia["N_TW"])
    q, st, td, tt = (instancia[k] for k in ("q", "st", "td", "tt"))
    a, b, aW, bW, Q = (instancia[k] for k in ("a", "b", "aW", "bW", "Q"))
    lo = {i: a[i] if i in NTW else aW for i in C}
    up = {i: b[i] if i in NTW else bW for i in C}

    m = Model(name=f"ConVRP_{instancia['nombre']}")

    def vbin(nombre):
        return (m.continuous_var(lb=0, ub=1, name=nombre) if relajado
                else m.binary_var(name=nombre))

    x = {(i, j, t, r): vbin(f"x_{i}_{j}_d{t}_r{r}") for (i, j, t) in A for r in R}
    z = {(i, r): vbin(f"z_{i}_r{r}") for i in C for r in R}
    s = {(i, t): m.continuous_var(lb=lo[i], ub=up[i], name=f"s_{i}_d{t}") for (i, t) in O}
    dW = {r: m.continuous_var(lb=aW, ub=bW, name=f"dW_r{r}") for r in R}
    rW = {(t, r): m.continuous_var(lb=aW, ub=bW, name=f"rW_d{t}_r{r}") for t in D for r in R}

    m.minimize(m.sum(td[i, j] * x[i, j, t, r] for (i, j, t) in A for r in R))

    entra, sale = {}, {}
    for (i, j, t) in A:
        sale.setdefault((i, t), []).append(j)
        entra.setdefault((j, t), []).append(i)

    for i in C:  # (2)
        m.add_constraint(m.sum(z[i, r] for r in R) == 1, ctname=f"asig_{i}")
    for (i, t) in O:  # (3), (4)
        for r in R:
            m.add_constraint(
                m.sum(x[k, i, t, r] for k in entra[i, t]) == z[i, r],
                ctname=f"entrada_{i}_d{t}_r{r}",
            )
            m.add_constraint(
                m.sum(x[i, k, t, r] for k in sale[i, t]) == z[i, r],
                ctname=f"salida_{i}_d{t}_r{r}",
            )
    for t in D:  # (5)
        for r in R:
            m.add_constraint(
                m.sum(x[0, j, t, r] for j in sale[0, t]) == 1,
                ctname=f"deposito_d{t}_r{r}",
            )
    for (i, j, t) in A:  # (7), (9), (10)
        if i == 0 and j == ret:
            continue
        for r in R:
            if i == 0:
                M = bW + tt[0, j] - lo[j]
                m.add_constraint(dW[r] + tt[0, j] <= s[j, t] + M * (1 - x[0, j, t, r]))
            elif j == ret:
                M = up[i] + st[i, t] + tt[i, ret] - aW
                m.add_constraint(
                    s[i, t] + st[i, t] + tt[i, ret]
                    <= rW[t, r] + M * (1 - x[i, ret, t, r]),
                )
            else:
                M = up[i] + st[i, t] + tt[i, j] - lo[j]
                m.add_constraint(
                    s[i, t] + st[i, t] + tt[i, j]
                    <= s[j, t] + M * (1 - x[i, j, t, r]),
                )
    for t in D:  # (14)
        for r in R:
            m.add_constraint(
                m.sum(q[i, t] * z[i, r] for i in C if q[i, t] > 0) <= Q,
                ctname=f"cap_d{t}_r{r}",
            )
    return m, {"x": x, "z": z, "s": s, "dW": dW, "rW": rW}


class _Val:
    """Imita una variable de Gurobi (.X / .Start) para reutilizar
    aplicar_inicio y validar_solucion con las variables de CPLEX."""
    X = None
    Start = None


def _sombra(vars_, valor=None):
    out = {}
    for k, d in vars_.items():
        out[k] = {}
        for key, var in d.items():
            o = _Val()
            if valor is not None:
                o.X = valor(var)
            out[k][key] = o
    return out


def start_cplex(nombre, m, vars_):
    archivo = PATH_GENERADAS / f"{nombre}_verificacion.txt"
    if not archivo.is_file():
        return
    rutas_ref, _ = solucion_de_verificacion(archivo)
    sombra = _sombra(vars_)
    aplicar_inicio(sombra, instancias[nombre], rutas_ref)
    ws = m.new_solution()
    for k, d in vars_.items():
        for key, var in d.items():
            if sombra[k][key].Start is not None:
                ws.add_var_value(var, sombra[k][key].Start)
    m.add_mip_start(ws)


# ======================================================================
# 5. Resolucion con parametros por defecto (solo TimeLimit) y logs
# ======================================================================
def _gap(obj, cota):
    """Gap relativo en %, misma definicion para ambos solvers."""
    if obj is None or cota is None:
        return None
    return 100 * abs(obj - cota) / max(abs(obj), 1e-10)


def resolver_gurobi(nombre, limite=600, usar_start=True):
    inst = instancias[nombre]
    lg_lp, lg_mip = (PATH_LOGS / f"{nombre}_gurobi_{k}.log" for k in ("lp", "mip"))
    lg_lp.unlink(missing_ok=True)
    lg_mip.unlink(missing_ok=True)
    out = {}

    m, v = construir_modelo(inst)
    m.update()
    lp = m.relax()
    lp.Params.OutputFlag, lp.Params.LogToConsole, lp.Params.LogFile = 1, 0, str(lg_lp)
    lp.optimize()
    assert lp.Status == GRB.OPTIMAL, "relajacion de Gurobi no optima"
    out["LP"] = dict(
        objetivo=lp.ObjVal, cota=lp.ObjVal, tiempo=lp.Runtime, nodos=0,
        estado="OPTIMO", valida="-",
    )

    m.Params.OutputFlag, m.Params.LogToConsole, m.Params.LogFile = 1, 0, str(lg_mip)
    m.Params.TimeLimit = limite
    if usar_start:
        cargar_start(nombre, v)
    m.optimize()
    res = dict(
        objetivo=None, cota=None, tiempo=m.Runtime, nodos=m.NodeCount,
        estado=ESTADOS.get(m.Status, str(m.Status)), valida="-",
    )
    if m.SolCount > 0:
        res["objetivo"], res["cota"] = m.ObjVal, m.ObjBound
        try:
            validar_solucion(inst, v)
            res["valida"] = "OK"
        except AssertionError as e:
            res["valida"] = f"FALLA: {e}"
    out["MIP"] = res
    return out


def resolver_cplex(nombre, limite=600, usar_start=True):
    inst = instancias[nombre]
    out = {}

    m, _ = construir_modelo_cplex(inst, relajado=True)
    with open(PATH_LOGS / f"{nombre}_cplex_lp.log", "w", encoding="utf-8") as fh:
        sol = m.solve(log_output=fh)
    assert sol is not None, f"relajacion de CPLEX sin solucion: {m.solve_details.status}"
    out["LP"] = dict(
        objetivo=sol.objective_value, cota=sol.objective_value,
        tiempo=m.solve_details.time, nodos=0, estado="OPTIMO", valida="-",
    )

    m, v = construir_modelo_cplex(inst)
    m.parameters.timelimit = limite
    if usar_start:
        start_cplex(nombre, m, v)
    with open(PATH_LOGS / f"{nombre}_cplex_mip.log", "w", encoding="utf-8") as fh:
        sol = m.solve(log_output=fh)
    det = m.solve_details
    txt = str(det.status).lower()
    estado = ("OPTIMO" if "optimal" in txt else "TIME_LIMIT" if "time limit" in txt
    else "INFACTIBLE" if "infeasible" in txt else str(det.status))
    res = dict(
        objetivo=None, cota=None, tiempo=det.time, nodos=det.nb_nodes_processed,
        estado=estado, valida="-",
    )
    if sol is not None:
        res["objetivo"], res["cota"] = sol.objective_value, det.best_bound
        try:
            validar_solucion(inst, _sombra(v, sol.get_value))
            res["valida"] = "OK"
        except AssertionError as e:
            res["valida"] = f"FALLA: {e}"
    out["MIP"] = res
    return out


# ======================================================================
# 6. Comparacion, validacion cruzada y exportacion
# ======================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instancias", nargs="+", default=list(INSTANCIAS_CFG))
    ap.add_argument("--limite", type=float, default=600, help="TimeLimit (s) del modelo entero")
    ap.add_argument("--sin-start", action="store_true", help="no usar MIP start en ningun solver")
    ap.add_argument("--regenerar", action="store_true", help="regenerar instancias")
    args = ap.parse_args()

    for p in (PATH_GENERADAS, PATH_RESULTADOS, PATH_LOGS):
        p.mkdir(parents=True, exist_ok=True)
    resumen = open(PATH_LOGS / "comparacion.log", "w", encoding="utf-8")

    def log(msg=""):
        print(msg)
        resumen.write(msg + "\n")
        resumen.flush()

    filas = []
    for nombre in args.instancias:
        cfg = INSTANCIAS_CFG[nombre]
        archivo = PATH_GENERADAS / f"{nombre}.txt"
        if args.regenerar or not archivo.is_file():
            generar(
                PATH_SOLOMON / cfg["source"], archivo, cfg["n"], cfg["days"],
                cfg["vehicles"], cfg["seed"], cfg["slack"],
            )
        instancias[nombre] = leer_multidia(archivo)
        inst = instancias[nombre]
        log(
            f"=== {nombre}: {len(inst['C'])} clientes, {len(inst['D'])} dias, "
            f"{len(inst['R'])} rutas, {len(inst['O'])} pedidos, limite={args.limite:g}s ===",
        )

        res = {
            "Gurobi": resolver_gurobi(nombre, args.limite, not args.sin_start),
            "CPLEX": resolver_cplex(nombre, args.limite, not args.sin_start),
        }
        for solver, r in res.items():
            for tipo in ("LP", "MIP"):
                d = r[tipo]
                filas.append(
                    {
                        "instancia": nombre, "solver": solver, "modelo": tipo,
                        "objetivo": d["objetivo"], "cota": d["cota"],
                        "gap_%": _gap(d["objetivo"], d["cota"]) if tipo == "MIP" else None,
                        "tiempo_s": d["tiempo"], "nodos": d["nodos"],
                        "estado": d["estado"], "valida": d["valida"],
                    },
                )

        # Validacion cruzada
        lg, lc = res["Gurobi"]["LP"]["objetivo"], res["CPLEX"]["LP"]["objetivo"]
        rel = abs(lg - lc) / max(abs(lg), 1e-10)
        log(
            f"[{nombre}] LP: Gurobi={lg:.6f} CPLEX={lc:.6f} dif.rel={rel:.2e} "
            + ("OK" if rel < 1e-6 else "<<< DIFERENCIA NOTORIA"),
        )
        mg, mc = res["Gurobi"]["MIP"], res["CPLEX"]["MIP"]
        if mg["objetivo"] is not None and mc["objetivo"] is not None:
            rel = abs(mg["objetivo"] - mc["objetivo"]) / max(abs(mg["objetivo"]), 1e-10)
            ambos = mg["estado"] == "OPTIMO" and mc["estado"] == "OPTIMO"
            log(
                f"[{nombre}] MIP: Gurobi={mg['objetivo']:.6f} ({mg['estado']}) "
                f"CPLEX={mc['objetivo']:.6f} ({mc['estado']}) dif.rel={rel:.2e} "
                + ("<<< DIFERENCIA NOTORIA" if ambos and rel > 2e-4 else ""),
            )
            for a, b, na, nb in ((mg, mc, "Gurobi", "CPLEX"), (mc, mg, "CPLEX", "Gurobi")):
                if a["objetivo"] < b["cota"] - 1e-4 * max(1, abs(b["cota"])):
                    log(f"[{nombre}] <<< incumbente de {na} menor que la cota de {nb}")

    df = pd.DataFrame(filas)
    df.round(6).to_csv(PATH_RESULTADOS / "comparacion_gurobi_vs_cplex.csv", index=False)
    log("\n" + df.round(4).to_string(index=False))
    resumen.close()


if __name__ == "__main__":
    main()
