"""Puntos 3 y 4. Ejecutar desde cualquier directorio: python analisis_34.py."""
import argparse
import ast
import csv
import hashlib
import json
import platform
import time
from pathlib import Path

import gurobipy as gp
from gurobipy import GRB

ROOT = Path(__file__).resolve().parent
MANUALES = {
    'baseline': {}, 'Cuts_0': {'Cuts': 0}, 'Presolve_0': {'Presolve': 0},
    'MIPFocus_1': {'MIPFocus': 1}, 'MIPFocus_3': {'MIPFocus': 3},
    'Heuristics_0': {'Heuristics': 0},
}


def cargar_funciones(notebook=ROOT / 'main.ipynb'):
    """Reutiliza funciones/imports del notebook sin regenerar datos ni resolverlo."""
    ns = {'__name__': 'modelo_notebook'}
    for cell in json.loads(Path(notebook).read_text(encoding='utf-8'))['cells']:
        if cell['cell_type'] != 'code':
            continue
        tree = ast.parse(''.join(cell['source']))
        nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom, ast.FunctionDef))]
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(notebook), 'exec'), ns)
    return ns


def csv_guardar(path, rows):
    if not rows:
        return
    with Path(path).open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def nuevo_modelo(api, inst, referencia, log, parametros=None, prm=None):
    m, variables, _ = api['construir_modelo'](inst, verbo=False)
    # El constructor heredado cambia Seed y MIPGap; no deben contaminar baseline.
    m.resetParams()
    if prm is not None:
        m.read(str(prm))
    for key, value in (parametros or {}).items():
        m.setParam(key, value)
    m.Params.Threads = 1
    m.Params.TimeLimit = 600
    m.Params.LogFile = str(log)
    rutas, _ = api['solucion_de_verificacion'](referencia)
    api['aplicar_inicio'](variables, inst, rutas)
    m.update()
    return m, variables


def resolver_corrida(api, inst, referencia, out, nombre, parametros=None, prm=None):
    m, variables = nuevo_modelo(api, inst, referencia, out / f'{nombre}.log', parametros, prm)
    try:
        m.optimize()
        row = {'configuracion': nombre, 'estado': m.Status, 'tiempo_s': m.Runtime,
               'objetivo': m.ObjVal if m.SolCount else None,
               'gap_pct': 100 * m.MIPGap if m.SolCount else None,
               'cota': m.ObjBound, 'nodos': m.NodeCount, 'soluciones': m.SolCount}
        if m.SolCount:
            api['validar_solucion'](inst, variables)
            m.write(str(out / f'{nombre}.sol'))
        m.write(str(out / f'{nombre}.prm'))
        return m, row
    except Exception:
        m.dispose()
        raise


def ranking(row):
    """Primero menor distancia, luego menor gap y finalmente menor tiempo."""
    return tuple(float('inf') if row[k] is None else row[k]
                 for k in ('objetivo', 'gap_pct', 'tiempo_s'))


def sensibilidad(m, inst, out, origen):
    if not m.SolCount:
        raise ValueError('Model.fixed() requiere un incumbente factible.')
    fixed = m.fixed()
    try:
        fixed.resetParams()
        fixed.Params.Threads = 1
        fixed.Params.TimeLimit = 600
        # Simplex produce una base para SARHS; no es una corrida de comparación MIP.
        fixed.Params.Method = 1
        fixed.Params.Presolve = 0
        fixed.Params.LogFile = str(out / 'sensibilidad.log')
        fixed.optimize()
        if fixed.Status != GRB.OPTIMAL:
            raise RuntimeError(f'LP fijado sin óptimo: estado {fixed.Status}; no se consultan duales.')
        rows = []
        for t in inst['D']:
            for r in inst['R']:
                c = fixed.getConstrByName(f'capacidad_d{t}_r{r}')
                if c is None:
                    raise ValueError('No se encontró la restricción de capacidad esperada.')
                carga = sum(inst['q'][i, t] * round(m.getVarByName(f'v_{i}_d{t}_r{r}').X)
                            for i in inst['C'] if (i, t) in inst['O'])
                rows.append({'origen': origen, 'dia': t, 'vehiculo': r, 'Q': c.RHS,
                             'carga': carga, 'holgura': c.Slack, 'precio_sombra': c.Pi,
                             'SARHSLow': c.SARHSLow, 'SARHSUp': c.SARHSUp})
        csv_guardar(out / 'sensibilidad_capacidad.csv', rows)
        max_load = max(row['carga'] for row in rows)
        text = f'''# Sensibilidad de capacidad — {inst['nombre']}

Solución seleccionada: {origen}. Objetivo MIP: {m.ObjVal:.8f}; gap: {m.MIPGap:.4%}.
Objetivo del LP fijado: {fixed.ObjVal:.8f}.
Capacidad original: {inst['Q']:g}; máxima carga asignada: {max_load:g}.

`Model.fixed()` fija TODAS las variables enteras (x, v, z). El objetivo solo
contiene x: queda constante mientras la solución fijada siga siendo factible.
Por eso se espera un precio sombra nulo en las filas de capacidad; se reportan
los valores realmente obtenidos en sensibilidad_capacidad.csv.

SARHSLow/SARHSUp describen cambios de UN lado derecho manteniendo los demás
coeficientes y lados derechos constantes y conservando la base óptima. No son
un intervalo global de optimalidad del MIP ni intervalos simultáneos. Valores
cercanos a 1e100 representan infinito en Gurobi. Los límites pueden depender
de la base elegida (degeneración).

Con las rutas fijadas, la capacidad física común mínima es la mayor carga,
{max_load:g}. Reducir Q por debajo de ella hace inviable esa asignación; aumentarlo
no cambia su distancia. Reoptimizar el MIP podría encontrar rutas diferentes
y mejorar el objetivo: el precio sombra del LP fijado no mide ese efecto.

En el modelo heredado, Q también aparece en cotas de u y restricciones MTZ
(carga acumulada). Cambiar solo RHS de capacidad NO equivale a cambiar Q en
todo el modelo. Para estudiar un Q global nuevo hay que reconstruir el modelo
con ese valor y resolver de nuevo. No se intersectan los rangos SARHS individuales.
'''
        (out / 'interpretacion_sensibilidad.md').write_text(text, encoding='utf-8')
        return rows
    finally:
        fixed.dispose()


def ejecutar_instancia(nombre, api, out, tune_limit):
    source = ROOT / 'data' / 'instancias' / f'{nombre}.txt'
    referencia = source.with_name(f'{nombre}_verificacion.txt')
    inst = api['leer_multidia'](source)
    out.mkdir(parents=True, exist_ok=True)
    metadata = {'instancia': nombre, 'sha256_instancia': hashlib.sha256(source.read_bytes()).hexdigest(),
                'sha256_notebook': hashlib.sha256((ROOT / 'main.ipynb').read_bytes()).hexdigest(),
                'gurobi': gp.gurobi.version(), 'python': platform.python_version(),
                'plataforma': platform.platform(), 'procesador': platform.processor(),
                'Threads': 1, 'TimeLimit': 600, 'TuneTimeLimit': tune_limit,
                'mip_start': 'Misma referencia para todas las corridas; no usar incumbentes anteriores',
                'criterio_manual': 'menor objetivo, luego gap, luego tiempo',
                'estado': 'en_progreso'}
    meta_path = out / 'metadata.json'
    meta_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    models, rows = {}, []
    try:
        for nombre_config, parametros in MANUALES.items():
            m, row = resolver_corrida(api, inst, referencia, out, nombre_config, parametros)
            models[nombre_config] = m
            rows.append(row)
            csv_guardar(out / 'comparacion.csv', rows)
        manual = min(rows[1:], key=ranking)
        tuner, _ = nuevo_modelo(api, inst, referencia, out / 'tuning.log')
        try:
            tuner.Params.TuneTimeLimit = tune_limit
            t0 = time.perf_counter()
            tuner.tune()
            metadata['tuning_tiempo_total_s'] = time.perf_counter() - t0
            metadata['TuneResultCount'] = tuner.TuneResultCount
            if tuner.TuneResultCount > 0:
                tuner.getTuneResult(0)
                prm = out / 'tuning_mejor.prm'
                tuner.write(str(prm))
                # Reconstrucción limpia: no heredar solución ni árbol del tuning.
                m, row = resolver_corrida(api, inst, referencia, out, 'tuned', prm=prm)
                models['tuned'] = m
                rows.append(row)
        finally:
            tuner.dispose()
        csv_guardar(out / 'comparacion.csv', rows)
        selected = [rows[0], manual] + [r for r in rows if r['configuracion'] == 'tuned']
        csv_guardar(out / 'comparacion_resumen.csv', selected)
        best = min((r for r in rows if r['objetivo'] is not None), key=ranking, default=None)
        if best is None:
            raise RuntimeError('Ninguna corrida produjo incumbente: sensibilidad pendiente.')
        sensibilidad(models[best['configuracion']], inst, out, best['configuracion'])
        lines = ['# Comparación del ajuste automático', '',
                 'Mejor manual excluye baseline. Criterio: objetivo, luego gap, luego tiempo.', '',
                 '| Configuración | Tiempo (s) | Gap (%) | Distancia |',
                 '|---|---:|---:|---:|']
        for row in selected:
            lines.append('| ' + ' | '.join(str(row[k]) for k in
                         ('configuracion', 'tiempo_s', 'gap_pct', 'objetivo')) + ' |')
        lines += ['', 'El tiempo de búsqueda del tuning se reporta por separado en metadata.json.',
                  'El tuner no garantiza una mejora. Una sola corrida no prueba superioridad general.',
                  'Si se alcanza 600 s, comparar distancia y gap; no interpretar tiempos similares como empate.',
                  f"Resultados retenidos por tune(): {metadata['TuneResultCount']}."]
        (out / 'comparacion.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        metadata['estado'] = 'completado'
    except Exception as exc:
        metadata['estado'] = 'error'
        metadata['error'] = str(exc)
        raise
    finally:
        meta_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
        for m in models.values():
            m.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instancias', nargs='+', choices=['I1', 'I2'], default=['I1', 'I2'])
    parser.add_argument('--tune-time-limit', type=float, default=1800)
    parser.add_argument('--salida', type=Path, default=ROOT / 'results' / 'analisis_34')
    args = parser.parse_args()
    if args.tune_time_limit <= 0:
        parser.error('TuneTimeLimit debe ser positivo.')
    api = cargar_funciones()
    for nombre in args.instancias:
        ejecutar_instancia(nombre, api, args.salida.resolve() / nombre, args.tune_time_limit)


if __name__ == '__main__':
    main()
