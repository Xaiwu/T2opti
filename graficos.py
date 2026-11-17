#Figuras de la presentación de la TC3.

import re
import sys
from pathlib import Path
from unittest import mock

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
import pandas as pd

# main.py importa los solvers; si no están, los simulamos (aquí no se resuelve nada).
for mod in ("gurobipy", "docplex", "docplex.mp", "docplex.mp.model"):
    try:
        __import__(mod)
    except ImportError:
        sys.modules[mod] = mock.MagicMock()
import main  # noqa: E402

OUT = Path("presentacion/figuras")
OUT.mkdir(parents=True, exist_ok=True)

# Colores
SURFACE = "#fcfcfb"
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#8a8984", "#e4e3df"
SOLVER = {"Gurobi": "#2a78d6", "CPLEX": "#eb6834"}
VEH = ["#1baf7a", "#6250d6", "#e34948"]
VEH_MARK = ["o", "s", "^"]  # cada vehículo con su forma, por si no se distingue el color
SEQ = LinearSegmentedColormap.from_list(
    "azul", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 11,
    "axes.edgecolor": GRID, "axes.labelcolor": INK2, "axes.titlecolor": INK,
    "xtick.color": INK2, "ytick.color": INK2, "axes.facecolor": SURFACE,
    "figure.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "mathtext.fontset": "cm",
})


def guardar(fig, nombre, transparente=False):
    fig.savefig(OUT / nombre, dpi=220, bbox_inches="tight", transparent=transparente)
    plt.close(fig)
    print("  ->", OUT / nombre)


# Mapa de cada día con las rutas de la solución de referencia
def fig_instancia(nombre):
    inst = main.leer_multidia(main.PATH_GENERADAS / f"{nombre}.txt")
    rutas, _ = main.solucion_de_verificacion(main.PATH_GENERADAS / f"{nombre}_verificacion.txt")
    C, D, R, ret = inst["C"], inst["D"], inst["R"], inst["retorno"]
    xy, q = inst["coordenadas"], inst["q"]
    veh = {i: r for (r, t), cl in rutas.items() for i in cl}
    qmax = max(q.values())

    fig, axes = plt.subplots(1, len(D), figsize=(5.2 * len(D), 5.4), sharex=True, sharey=True)
    for ax, t in zip(axes, D):
        activos = [i for i in C if q[i, t] > 0]
        for r in R:
            ruta = [0] + rutas.get((r, t), []) + [ret]
            ax.plot([xy[k][0] for k in ruta], [xy[k][1] for k in ruta],
                    color=VEH[r - 1], lw=2, zorder=2, solid_capstyle="round")
        for i in C:
            x, y = xy[i]
            if q[i, t] > 0:
                r = veh[i]
                ax.scatter(x, y, s=60 + 260 * q[i, t] / qmax, marker=VEH_MARK[r - 1],
                           color=VEH[r - 1], edgecolor=SURFACE, linewidth=2, zorder=3)
            else:
                ax.scatter(x, y, s=70, marker="o", facecolor=SURFACE,
                           edgecolor=MUTED, linewidth=1.4, linestyle=(0, (2, 1)), zorder=3)
            ax.annotate(str(i), (x, y), xytext=(6, 5), textcoords="offset points",
                        fontsize=8, color=INK2 if q[i, t] > 0 else MUTED, zorder=4)
        ax.scatter(*xy[0], s=220, marker="s", color=INK, edgecolor=SURFACE, linewidth=2, zorder=5)
        ax.set_title(f"Día {t}: {len(activos)} de {len(C)} clientes con pedido",
                     fontsize=12, loc="left")
        ax.set_aspect("equal", adjustable="box")
        ax.tick_params(labelsize=9)

    handles = [Line2D([], [], color=VEH[r - 1], lw=2, marker=VEH_MARK[r - 1], markersize=9,
                      markeredgecolor=SURFACE, label=f"Vehículo {r}") for r in R]
    handles += [
        Line2D([], [], ls="", marker="o", markerfacecolor=SURFACE, markeredgecolor=MUTED,
               markersize=8, label="Sin pedido ese día"),
        Line2D([], [], ls="", marker="s", color=INK, markersize=10, label="Depósito"),
    ]
    fig.legend(handles=handles, loc="upper center", ncol=len(handles), frameon=False,
               bbox_to_anchor=(0.5, 1.02), fontsize=10)
    fig.text(0.5, -0.02, f"{nombre} (base Solomon "
             f"{Path(main.INSTANCIAS_CFG[nombre]['source']).stem}) · rutas = solución de "
             "referencia usada como MIP start · tamaño del marcador ∝ demanda",
             ha="center", fontsize=9, color=INK2)
    guardar(fig, f"{nombre}_instancia.png")

    n_ped = len(inst["O"])
    no_todos = sum(1 for i in C if any(q[i, t] == 0 for t in D))
    return dict(instancia=nombre, clientes=len(C), dias=len(D), rutas=len(R),
                pedidos=n_ped, posibles=len(C) * len(D), clientes_sin_pedido_algun_dia=no_todos,
                pedidos_por_dia=[sum(q[i, t] > 0 for i in C) for t in D],
                Q=inst["Q"], cierre=inst["bW"])


# Demanda de cada cliente por día
def fig_demanda(nombres):
    insts = [main.leer_multidia(main.PATH_GENERADAS / f"{n}.txt") for n in nombres]
    vmax = max(max(i["q"].values()) for i in insts)
    fig, axes = plt.subplots(1, len(insts), figsize=(4.2 * len(insts), 9),
                             gridspec_kw={"width_ratios": [1] * len(insts)})
    for ax, nombre, inst in zip(axes, nombres, insts):
        C, D = inst["C"], inst["D"]
        ax.grid(False)
        for y, i in enumerate(C):
            for x, t in enumerate(D):
                v = inst["q"][i, t]
                if v > 0:
                    col = SEQ(0.15 + 0.85 * v / vmax)
                    ax.add_patch(plt.Rectangle((x + 0.04, y + 0.06), 0.92, 0.88, color=col, lw=0))
                    ax.text(x + 0.5, y + 0.5, f"{v:g}", ha="center", va="center", fontsize=7.5,
                            color="white" if v / vmax > 0.45 else INK)
                else:
                    ax.add_patch(plt.Rectangle((x + 0.04, y + 0.06), 0.92, 0.88, facecolor=SURFACE,
                                               edgecolor=MUTED, hatch="////", lw=0.6))
        ax.set_xlim(0, len(D))
        ax.set_ylim(len(C), 0)
        ax.set_xticks([k + 0.5 for k in range(len(D))], [f"Día {t}" for t in D])
        ax.set_yticks([k + 0.5 for k in range(len(C))], C, fontsize=7.5)
        ax.tick_params(length=0)
        for s in ax.spines.values():
            s.set_visible(False)
        ped = len(inst["O"])
        ax.set_title(f"{nombre}: {ped} de {len(C) * len(D)} pedidos posibles", loc="left", fontsize=12)
        ax.set_ylabel("Cliente")
    fig.text(0.5, 0.005, "Celdas rayadas = el cliente no pide ese día (no se visita; sin arcos ni (3)–(4))",
             ha="center", fontsize=9, color=INK2)
    guardar(fig, "demanda_multidia.png")


# Gurobi vs. CPLEX: relajación y modelo entero
def fig_lp(df):
    lp = df[df.modelo == "LP"]
    inst = list(lp.instancia.unique())
    fig, ax = plt.subplots(figsize=(6.5, 4))
    w = 0.36
    for k, solver in enumerate(SOLVER):
        vals = [lp[(lp.instancia == n) & (lp.solver == solver)].tiempo_s.iloc[0] * 1000 for n in inst]
        xs = [p + (k - 0.5) * (w + 0.03) for p in range(len(inst))]
        ax.bar(xs, vals, width=w, color=SOLVER[solver], label=solver, zorder=2)
        for x, v in zip(xs, vals):
            ax.annotate(f"{v:.0f} ms", (x, v), xytext=(0, 4), textcoords="offset points",
                        ha="center", fontsize=10, color=INK)
    objs = [lp[lp.instancia == n].objetivo.iloc[0] for n in inst]
    ax.set_xticks(range(len(inst)), [f"{n}\nz_LP = {o:.4f} (ambos)" for n, o in zip(inst, objs)])
    ax.set_ylabel("Tiempo de la relajación (ms)")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False, loc="upper left")
    ax.set_title("Relajación continua: mismo objetivo, tiempo en milisegundos", loc="left", fontsize=12)
    guardar(fig, "lp_comparacion.png")


def fig_mip(df):
    mip = df[df.modelo == "MIP"]
    lp = df[df.modelo == "LP"]
    inst = list(mip.instancia.unique())
    fig, axes = plt.subplots(len(inst), 1, figsize=(9, 2.3 * len(inst) + 0.6))
    for ax, n in zip(axes, inst):
        z_lp = lp[lp.instancia == n].objetivo.iloc[0]
        for k, solver in enumerate(["CPLEX", "Gurobi"]):
            row = mip[(mip.instancia == n) & (mip.solver == solver)].iloc[0]
            c = SOLVER[solver]
            ax.plot([row.cota, row.objetivo], [k, k], color=c, lw=6, alpha=0.35,
                    solid_capstyle="round", zorder=2)
            ax.scatter([row.cota], [k], marker="|", s=300, color=c, linewidth=3, zorder=3)
            ax.scatter([row.objetivo], [k], marker="o", s=90, color=c, edgecolor=SURFACE,
                       linewidth=2, zorder=4)
            ax.annotate(f"cota {row.cota:.2f}", (row.cota, k), xytext=(0, -16),
                        textcoords="offset points", ha="center", fontsize=9, color=INK2)
            ax.annotate(f"obj {row.objetivo:.2f}", (row.objetivo, k), xytext=(0, 12),
                        textcoords="offset points", ha="center", fontsize=9, color=INK2)
            ax.annotate(f"gap {row['gap_%']:.2f} %", (row.objetivo, k), xytext=(12, 0),
                        textcoords="offset points", va="center", fontsize=10.5, color=INK,
                        fontweight="bold")
        ax.axvline(z_lp, color=MUTED, ls=(0, (3, 2)), lw=1.2, zorder=1)
        ax.annotate(f"z_LP {z_lp:.2f}", (z_lp, 1.45), ha="left", va="center", fontsize=9,
                    color=INK2, xytext=(4, 0), textcoords="offset points")
        ax.set_yticks([0, 1], ["CPLEX", "Gurobi"])
        ax.set_ylim(-0.7, 1.7)
        xmax = mip[mip.instancia == n].objetivo.max()
        ax.set_xlim(z_lp - 0.03 * xmax, xmax * 1.09)
        ax.grid(axis="y", visible=False)
        ax.set_title(f"{n}: intervalo [cota, objetivo] a los 600 s", loc="left", fontsize=12)
    axes[-1].set_xlabel("Distancia total")
    fig.tight_layout()
    guardar(fig, "mip_intervalos.png")

    # Nodos explorados
    fig, ax = plt.subplots(figsize=(6.5, 4))
    w = 0.36
    for k, solver in enumerate(SOLVER):
        vals = [mip[(mip.instancia == n) & (mip.solver == solver)].nodos.iloc[0] for n in inst]
        xs = [p + (k - 0.5) * (w + 0.03) for p in range(len(inst))]
        ax.bar(xs, [v / 1000 for v in vals], width=w, color=SOLVER[solver], label=solver, zorder=2)
        for x, v in zip(xs, vals):
            ax.annotate(f"{v / 1e6:.2f} M".replace(".", ",") if v >= 1e6 else f"{v / 1000:.0f} k", (x, v / 1000), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=10, color=INK)
    ax.set_xticks(range(len(inst)), inst)
    ax.set_ylabel("Nodos explorados (miles)")
    ax.grid(axis="x", visible=False)
    ax.legend(frameon=False)
    ax.set_title("Nodos de branch & bound en 600 s", loc="left", fontsize=12)
    guardar(fig, "mip_nodos.png")


# Cómo avanzan el incumbente y la cota (sacado de los logs)
NUM = r"[-+]?\d+(?:\.\d+)?(?:e[-+]?\d+)?"


def traza_gurobi(path):
    pts = []
    pat = re.compile(rf"\s({NUM})\s+({NUM})\s+[\d.]+%\s+\S+\s+(\d+)s\s*$")
    for linea in Path(path).read_text(encoding="utf-8").splitlines():
        m = pat.search(linea)
        if m:
            pts.append((float(m.group(3)), float(m.group(1)), float(m.group(2))))
    fin = re.search(rf"Best objective ({NUM}), best bound ({NUM})", Path(path).read_text())
    runtime = re.search(r"in ([\d.]+) seconds", Path(path).read_text())
    if fin and runtime:
        pts.append((float(runtime.group(1)), float(fin.group(1)), float(fin.group(2))))
    return pts


def traza_cplex(path, t_final):
    pts, ultimo = [], None
    texto = Path(path).read_text(encoding="utf-8")
    bound_prev = None
    for linea in texto.splitlines():
        s = linea.strip()
        if s.startswith("Elapsed time"):
            t = float(re.search(r"=\s*([\d.]+) sec", s).group(1))
            if ultimo:
                pts.append((t, *ultimo))
            continue
        m = re.search(rf"({NUM})\s+(Cuts: \d+|{NUM}|\S+: \d+)\s+\d+\s+[\d.]+%\s*$", s)
        if m and re.match(r"^\*?\s*\d+\+?\s", s):
            inc = float(m.group(1))
            try:
                bound_prev = float(m.group(2))
            except ValueError:
                pass
            if bound_prev is not None:
                ultimo = (inc, bound_prev)
    if ultimo:
        pts.append((t_final, *ultimo))
    return pts


def fig_convergencia(df, nombres):
    mip = df[df.modelo == "MIP"]
    fig, axes = plt.subplots(1, len(nombres), figsize=(6.2 * len(nombres), 4.6))
    for ax, n in zip(axes, nombres):
        fila = lambda s: mip[(mip.instancia == n) & (mip.solver == s)].iloc[0]
        trazas = {
            "Gurobi": traza_gurobi(main.PATH_LOGS / f"{n}_gurobi_mip.log"),
            "CPLEX": traza_cplex(main.PATH_LOGS / f"{n}_cplex_mip.log", fila("CPLEX").tiempo_s),
        }
        for solver, pts in trazas.items():
            r = fila(solver)
            # el último punto sale del CSV
            pts = [p for p in pts if p[0] > 0] + [(r.tiempo_s, r.objetivo, r.cota)]
            # el incumbente solo baja y la cota solo sube
            ts, inc, cot = [], [], []
            for t, i, b in pts:
                ts.append(max(t, ts[-1] if ts else t))
                inc.append(min(i, inc[-1]) if inc else i)
                cot.append(max(b, cot[-1]) if cot else b)
            ax.step(ts, inc, where="post", color=SOLVER[solver], lw=2,
                    label=f"{solver}: incumbente")
            ax.step(ts, cot, where="post", color=SOLVER[solver], lw=2,
                    ls=(0, (4, 2)), label=f"{solver}: cota inferior")
        ax.set_xscale("log")
        ax.set_xlim(0.5, 650)
        ax.set_xlabel("Tiempo (s, escala log)")
        ax.set_ylabel("Distancia total")
        ax.set_title(f"{n}: incumbente (—) y cota (- -)", loc="left", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="upper center", ncol=4, frameon=False, fontsize=10,
               bbox_to_anchor=(0.5, 1.0))
    guardar(fig, "convergencia.png")


# El modelo como imagen, porque Canva no escribe bien las fórmulas
DEFINICIONES = [
    ("h", "Conjuntos"),
    ("l", r"$C=\{1,\ldots,n\}$ clientes;  $N=\{0\}\cup C\cup\{n+1\}$ (salida y retorno al depósito);  $D$ días;  $R$ rutas (vehículos)"),
    ("l", r"$O=\{(i,t)\in C\times D:\ q_{it}>0\}$ pedidos;   $C_t=\{i\in C:\ q_{it}>0\}$ clientes con pedido el día $t$"),
    ("l", r"$A_t=\{(0,j),(j,n+1): j\in C_t\}\ \cup\ \{(i,j): i,j\in C_t,\ i\neq j\}\ \cup\ \{(0,n+1)\}$   arcos del día $t$"),
    ("h", "Parámetros"),
    ("l", r"$td_{ij}$ distancia,  $tt_{ij}$ tiempo de viaje,  $st_{it}$ servicio,  $q_{it}$ demanda,  $Q$ capacidad"),
    ("l", r"$[a_i,b_i]$ ventana del cliente $i$;  $[a_W,b_W]$ horario del depósito"),
    ("h", "Variables"),
    ("l", r"$x^r_{ijt}\in\{0,1\}$: la ruta $r$ recorre $(i,j)\in A_t$ el día $t$;   $z^r_i\in\{0,1\}$: el cliente $i$ se asigna a la ruta $r$"),
    ("l", r"$W_{it}\geq 0$ inicio de servicio de $(i,t)\in O$;  $dW_r$ salida de la ruta $r$;  $rW_{tr}$ retorno de la ruta $r$ el día $t$"),
]

RESTRICCIONES = [
    ("(1)", r"$\min\ \sum_{t\in D}\ \sum_{(i,j)\in A_t}\ \sum_{r\in R}\ td_{ij}\,x^r_{ijt}$", ""),
    ("(2)", r"$\sum_{r\in R} z^r_i = 1$", r"$\forall\, i\in C$"),
    ("(3)", r"$\sum_{k:(k,i)\in A_t} x^r_{kit} = z^r_i$", r"$\forall\,(i,t)\in O,\ r\in R$"),
    ("(4)", r"$\sum_{k:(i,k)\in A_t} x^r_{ikt} = z^r_i$", r"$\forall\,(i,t)\in O,\ r\in R$"),
    ("(5)", r"$\sum_{j:(0,j)\in A_t} x^r_{0jt} = 1$", r"$\forall\, t\in D,\ r\in R$"),
    ("(7)", r"$W_{it}+st_{it}+tt_{ij} \leq W_{jt} + M_{ij}\,(1-x^r_{ijt})$", r"$\forall\, t,\ (i,j)\in A_t,\ i,j\in C_t,\ r$"),
    ("(8)", r"$a_i \leq W_{it} \leq b_i$", r"$\forall\,(i,t)\in O$"),
    ("(9)", r"$dW_r + tt_{0j} \leq W_{jt} + M_{0j}\,(1-x^r_{0jt})$", r"$\forall\, t,\ j\in C_t,\ r$"),
    ("(10)", r"$W_{it}+st_{it}+tt_{i,n+1} \leq rW_{tr} + M_{i,n+1}\,(1-x^r_{i,n+1,t})$", r"$\forall\, t,\ i\in C_t,\ r$"),
    ("(11–12)", r"$a_W \leq dW_r \leq b_W,\quad a_W \leq rW_{tr} \leq b_W$", r"$\forall\, t\in D,\ r\in R$"),
    ("(14)", r"$\sum_{i\in C_t} q_{it}\, z^r_i \leq Q$", r"$\forall\, t\in D,\ r\in R$"),
    ("(16)", r"$x^r_{ijt},\ z^r_i\in\{0,1\};\quad W_{it},\ dW_r,\ rW_{tr}\geq 0$", ""),
]


def fig_formulacion():
    # Conjuntos, parámetros y variables
    ancho, paso_in = 16, 0.56
    alto = paso_in * len(DEFINICIONES) + 0.2
    fig = plt.figure(figsize=(ancho, alto))
    y = 1 - 0.1 / alto
    for tipo, txt in DEFINICIONES:
        if tipo == "h":
            fig.text(0.0, y, txt, fontsize=19, fontweight="bold", color=INK, va="top")
        else:
            fig.text(0.02, y, txt, fontsize=18, color=INK, va="top")
        y -= paso_in / alto
    guardar(fig, "formulacion_definiciones.png", transparente=True)

    # Objetivo y restricciones en dos columnas (los límites de las sumas van al lado)
    filas = {num: (re.sub(r"\\sum_(\{[^}]*\{[^}]*\}[^}]*\}|\{[^}]*\})", r"{\\sum}_\1\\,", expr), dom)
             for num, expr, dom in RESTRICCIONES}
    izq = ["(1)", "(2)", "(3)", "(4)", "(5)", "(14)", "(16)"]
    der = ["(7)", "(8)", "(9)", "(10)", "(11–12)"]
    fila_in, bigm_in = 0.95, 0.5
    alto = max(len(izq) * fila_in, len(der) * fila_in + 0.6 + 3 * bigm_in) + 0.2
    fig = plt.figure(figsize=(ancho, alto))
    for x0, claves in ((0.0, izq), (0.5, der)):
        y = 1 - 0.1 / alto
        for num in claves:
            expr, dom = filas[num]
            fig.text(x0, y, num, fontsize=17, color=INK2, va="top")
            fig.text(x0 + 0.068, y, expr, fontsize=20, color=INK, va="top")
            if dom:
                fig.text(x0 + 0.068, y - 0.52 / alto, dom, fontsize=16, color=INK2, va="top")
            y -= fila_in / alto
    y -= 0.15 / alto
    fig.text(0.5, y, "Big-M por arco", fontsize=17, fontweight="bold", color=INK2, va="top")
    for linea in (r"$M_{ij}=b_i+st_{it}+tt_{ij}-a_j$",
                  r"$M_{0j}=b_W+tt_{0j}-a_j$",
                  r"$M_{i,n+1}=b_i+st_{it}+tt_{i,n+1}-a_W$"):
        y -= bigm_in / alto
        fig.text(0.568, y, linea, fontsize=18, color=INK2, va="top")
    guardar(fig, "formulacion_restricciones.png", transparente=True)


def main_graficos():
    nombres = list(main.INSTANCIAS_CFG)
    print("Instancias:")
    resumen = [fig_instancia(n) for n in nombres]
    fig_demanda(nombres)
    df = pd.read_csv(main.PATH_RESULTADOS / "comparacion_gurobi_vs_cplex.csv")
    print("Comparacion:")
    fig_lp(df)
    fig_mip(df)
    fig_convergencia(df, nombres)
    print("Formulacion:")
    fig_formulacion()
    print()
    print(pd.DataFrame(resumen).to_string(index=False))


if __name__ == "__main__":
    main_graficos()
