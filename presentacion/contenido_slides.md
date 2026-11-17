# TC3 — Contenido de la presentación (para armar en Canva)

Pitch de 5 min, 9 slides (~30–35 s cada una). Las figuras están en `presentacion/figuras/`.
Todas las cifras vienen de `logs/comparacion.log` y `results/comparacion_gurobi_vs_cplex.csv`.
El guion de cada slide va en *cursiva*.

---

## Slide 1 — Portada

**Ruteo consistente de vehículos con ventanas de tiempo**
Tarea Computacional 3: Gurobi vs. CPLEX

Integrantes Grupo 14: Javier Campos Contreras · Sebastián Garcias Cabrera · Jesús Guevara Salcedo · Felipe Ruskin Azócar
Profesora: Rosa Medina

---

## Slide 2 — Contexto y objetivo

**Problema** (el mismo de la TC2, basado en Campelo et al., 2019)
- Una distribuidora farmacéutica abastece farmacias desde un depósito con 3 vehículos durante 3 días.
- Los pedidos cambian de un día a otro.
- Cada farmacia conserva **el mismo vehículo y conductor** todo el horizonte. Las visitas y su orden sí pueden cambiar.
- Se respetan las ventanas horarias y la capacidad, y se **minimiza la distancia total**.

**Qué hicimos en la TC3**
1. Corregimos el modelo y las instancias según la pauta.
2. Reimplementamos el modelo en **CPLEX (docplex)**.
3. Comparamos **Gurobi vs. CPLEX** en la relajación y en el modelo entero.

*Guion: "Retomamos el ruteo consistente de la TC2. La asignación cliente–vehículo es fija, pero las rutas diarias pueden cambiar. En esta tarea llevamos el mismo modelo a CPLEX y comparamos los dos solvers."*

---

## Slide 3 — Correcciones respecto a la TC2

| Sugerencia de la pauta | Cómo queda en la TC3 |
|---|---|
| No forzar flujo (3)–(4) a clientes sin demanda | (3)–(4) se imponen **solo para (i,t) ∈ O**. Los arcos del día t solo conectan clientes con pedido ese día (C_t), así que un cliente sin pedido **no tiene arcos y no se visita**. Se agrega el arco (0, n+1) para permitir una ruta vacía. |
| Tamaño mínimo (15 clientes, 3 días, 3 rutas) | I1: **20** clientes · I2: **30** clientes; ambas con 3 días y 3 rutas. |
| Estructura multi-día real | **12 de 20** clientes de I1 y **14 de 30** de I2 no piden algún día. Pedidos: 48 de 60 en I1 y 76 de 90 en I2. Clientes activos por día: I1 17/16/15, I2 26/25/25. |
| Formulación algebraica en la presentación | Modelo completo: conjuntos, parámetros, variables, función objetivo y restricciones (slide 4). |
| Misma configuración en ambos solvers | Parámetros por defecto en Gurobi y CPLEX, con solo **TimeLimit = 600 s**, en el mismo PC y con el mismo MIP start. (En la TC2 usábamos Threads = 1 y variantes de parámetros.) |

Validación: cada solución entera se revisa con un validador independiente (`validar_solucion`) que comprueba cobertura, consistencia de vehículo, capacidad, ventanas y tiempos de viaje y retorno. **Las 4 soluciones pasan.**

*Guion: "Lo principal: un cliente sin pedido ese día no aparece en la red de ese día, así que no se le exige entrada ni salida. Además mostramos que la demanda varía de verdad entre días y escribimos el modelo completo."*

---

## Slide 4 — Modelo matemático (PLEM reducido de Campelo et al., 2019)

Imágenes: `formulacion_definiciones.png` (conjuntos, parámetros, variables) y `formulacion_restricciones.png` (objetivo y restricciones). Ambas tienen fondo transparente.

Si no caben juntas, usar dos slides (4a: definiciones; 4b: objetivo y restricciones) o achicar las definiciones.

Resumen textual por si se necesita:
- (1) Minimizar la distancia total.
- (2) Cada cliente se asigna a una única ruta.
- (3)–(4) Entrada y salida de cada pedido (i,t) ∈ O en la ruta asignada.
- (5) Cada ruta sale una vez del depósito cada día (puede ir directo de 0 a n+1).
- (7), (9), (10) Secuencia temporal con Big-M por arco. También eliminan subtours, porque st > 0.
- (8) Ventanas de tiempo; (11)–(12) horario del depósito.
- (14) Capacidad por ruta y día.
- (16) Dominio de las variables.
- Se omiten (6), (13) y (15) del paper: liberación de pedidos, varios periodos de despacho y duración máxima de ruta, que están fuera del alcance.

*Guion: "z^r_i fija la ruta de cada cliente para todo el horizonte, y x^r_ijt define el recorrido diario. Los Big-M se calculan por arco con las ventanas, lo que da el valor más ajustado posible."*

---

## Slide 5 — Instancias

| Característica | I1 | I2 |
|---|---|---|
| Base Solomon | R108 | R110 |
| Clientes | 20 | 30 |
| Días / rutas | 3 / 3 | 3 / 3 |
| Pedidos (de los posibles) | 48 / 60 | 76 / 90 |
| Clientes que no piden algún día | 12 | 14 |
| Variables binarias | 2.523 | 6.105 |
| Restricciones | 2.780 | 6.510 |
| Capacidad Q | 98 | 154 |
| Cierre del depósito | 230 | 296 |
| Semilla | 42 | 43 |

Figuras:
- `I1_instancia.png` / `I2_instancia.png`: un panel por día. Los clientes sin pedido aparecen huecos y las rutas de la solución de referencia (el MIP start) van coloreadas por vehículo. **El mismo color en los 3 días muestra la consistencia.**
- `demanda_multidia.png`: mapa de calor cliente × día; las celdas rayadas son días sin pedido.

Cómo se construyeron (igual que en la TC2): los primeros n clientes de Solomon; demanda = demanda original × U(0,8; 1,2) en 2 o 3 días; Q = mayor carga de la solución de referencia + 5 %; ventanas ampliadas solo donde hizo falta para que la referencia fuera factible.

*Guion: "En cada día desaparecen clientes, y las rutas cambian de forma pero el color de cada cliente no cambia."*

---

## Slide 6 — Validación cruzada: relajación continua

Gurobi usa `model.relax()` y CPLEX tiene x y z continuas en [0, 1].

| Instancia | z_LP Gurobi | z_LP CPLEX | Dif. relativa | Tiempo Gurobi | Tiempo CPLEX |
|---|---|---|---|---|---|
| I1 | 585,0479 | 585,0479 | 6 × 10⁻¹⁶ | 25 ms | 11 ms |
| I2 | 798,1044 | 798,1044 | 1 × 10⁻¹⁵ | 54 ms | 22 ms |

Figura: `lp_comparacion.png`.

- **El objetivo es idéntico hasta la precisión de máquina**, así que los dos solvers resuelven exactamente el mismo modelo.
- CPLEX es ~2× más rápido en el LP, pero hablamos de milisegundos: en la práctica no cambia nada.
- La brecha LP frente al mejor entero es grande: 32,8 % (I1) y 41,0 % (I2). Es la relajación débil típica de los Big-M.

*Guion: "La relajación sirve como prueba de que los dos modelos son iguales: mismo valor con 15 decimales. Si hubiéramos escrito mal una restricción en CPLEX, este número sería distinto."*

---

## Slide 7 — Modelo entero: Gurobi vs. CPLEX

Configuración por defecto, TimeLimit = 600 s, mismo PC (AMD Ryzen 5 5600X, 12 hilos, Linux), mismo MIP start (I1: 1077,97; I2: 1440,88). Versiones: Gurobi 13.0.3 y CPLEX 22.2.

| Instancia | Solver | Objetivo | Cota | Gap | Tiempo | Nodos | Estado |
|---|---|---|---|---|---|---|---|
| I1 | Gurobi | **870,55** | **853,03** | **2,01 %** | 600 s | 1.395.177 | Límite de tiempo |
| I1 | CPLEX | 876,55 | 749,43 | 14,50 % | 600 s | 605.213 | Límite de tiempo |
| I2 | Gurobi | 1357,80 | **1069,64** | **21,22 %** | 600 s | 181.538 | Límite de tiempo |
| I2 | CPLEX | **1352,91** | 1005,42 | 25,68 % | 600 s | 341.973 | Límite de tiempo |

Figuras: `mip_intervalos.png` (intervalo [cota, objetivo] y gap) y `mip_nodos.png`. Opcional: `convergencia.png`.

*Guion: "Ninguno prueba optimalidad en 10 minutos. En I1 Gurobi gana claramente: gap de 2 % frente a 14,5 %. En I2 CPLEX encuentra una ruta algo más corta, pero Gurobi sigue teniendo mejor cota."*

---

## Slide 8 — Discusión: ¿por qué difieren?

Figura sugerida: `convergencia.png` (incumbente y cota en el tiempo).

- **Los resultados son consistentes entre sí.** El incumbente de cada solver es mayor o igual que la cota del otro, y los dos pasan el validador. El óptimo está en **[853,03; 870,55]** para I1 y en **[1069,64; 1352,91]** para I2.
- **Mismo LP, distinto branch & cut.** La raíz es la misma (585,05), pero cada solver hace su propio presolve, usa distintos cortes, heurísticas y reglas de ramificación:
  - Gurobi cierra la cota más rápido: en I1 pasa de 769 a 853 entre los 100 y los 600 s. Sus logs muestran muchos cortes de *implied bound*, *flow cover*, *RLT* y *relax-and-lift*.
  - CPLEX se apoya sobre todo en cortes MIR (887) y GUB cover (301) en I1. Su cota sube más lento.
- **La cantidad de nodos no explica la diferencia.** En I1 Gurobi explora 2,3× más nodos; en I2 CPLEX explora 1,9× más y aun así su cota queda más baja.
- **El incumbente depende de la búsqueda.** Las diferencias en el objetivo son pequeñas (0,69 % en I1 y 0,36 % en I2), y cada solver gana en una instancia: se deben a las heurísticas, no al modelo.
- **Por qué cuesta cerrar el gap en los dos solvers:**
  1. **Big-M débil.** Con x fraccionario las restricciones de tiempo casi no restringen, y la brecha LP es de 33–41 %.
  2. **Simetría.** Con 3 vehículos idénticos hay 3! = 6 soluciones equivalentes, y el árbol las recorre todas.
  3. **Tamaño.** I2 tiene 2,4× más binarias que I1, y ahí el gap queda por sobre 20 % en ambos solvers.

*Guion: "La diferencia no es un error: los dos llegan a soluciones válidas y coherentes entre sí. Gurobi es mejor probando la cota y CPLEX encontró una mejor ruta en I2. La dificultad común viene de la formulación: Big-M y simetría."*

---

## Slide 9 — Conclusiones y organización del equipo

**Conclusiones**
- El modelo corregido entrega **el mismo LP en ambos solvers**, lo que valida la implementación en CPLEX.
- En el modelo entero **Gurobi obtiene mejores cotas y menor gap** en las dos instancias. **CPLEX encontró el mejor incumbente en I2.**
- Ninguno prueba optimalidad en 600 s. Para mejorar habría que actuar sobre la formulación, no sobre el solver: restricciones de ruptura de simetría (ordenar vehículos) y Big-M más ajustados o una formulación de flujo de tiempo.
- Limitaciones: solo dos instancias, una corrida por solver (con 12 hilos y límite de tiempo puede variar algo) y un MIP start común a ambos.

**Organización del equipo** *(confirmar o ajustar los roles)*

| Integrante | Responsabilidad |
|---|---|
| Javier Campos Contreras | Formulación algebraica y correcciones del modelo |
| Jesús Guevara Salcedo | Instancias multidía, solución de referencia y gráficos |
| Felipe Ruskin Azócar | Implementación en Gurobi y CPLEX, corridas y logs |
| Sebastián Garcias Cabrera | Comparación de solvers, discusión y conclusiones |

Cierre: **Muchas gracias**

---

## Notas para el armado

- Colores: **Gurobi = azul #2A78D6** y **CPLEX = naranja #EB6834** en todas las figuras. Si el texto de la slide los destaca, usar los mismos.
- Los PNG de las fórmulas tienen fondo transparente y tinta negra: ponerlos sobre un fondo claro.
- Para regenerar las figuras: `python graficos.py` (no necesita los solvers).
