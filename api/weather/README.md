# api/weather — previsão solar e meteorologia

Esta pasta contém os scripts que:

1. preveem quanta energia cada instalação solar vai produzir nos próximos 7 dias;
2. guardam o tempo que realmente fez (histórico);
3. desenham o gráfico da produção prevista para amanhã;
4. comparam a previsão com o que os painéis "produziram".

Os dados meteorológicos vêm da [Open-Meteo](https://open-meteo.com/) (gratuita, não precisa de chave). Os resultados ficam numa base de dados PostgreSQL.

## Índice

- [Preparação (só da primeira vez)](#preparação-só-da-primeira-vez)
- [Resumo dos comandos](#resumo-dos-comandos)
- [Ficheiros de configuração](#ficheiros-de-configuração)
- [solar_forecast.py](#solar_forecastpy--previsão-da-produção-solar)
- [weather_history.py](#weather_historypy--histórico-do-tempo)
- [plot_tomorrow.py](#plot_tomorrowpy--gráfico-de-amanhã)
- [forecast_check.py](#forecast_checkpy--previsão-vs-produzido)
- [db.py](#dbpy--ligação-à-base-de-dados)
- [Tabelas da base de dados](#tabelas-da-base-de-dados)
- [Testes](#testes)
- [Erros comuns](#erros-comuns)

---

## Preparação (só da primeira vez)

Todos os comandos correm a partir da **pasta `Projeto/`** (a raiz do repositório), e não de dentro de `api/weather/`.

**1. Instalar as bibliotecas Python**

```
pip install -r requirements.txt
```

**2. Criar o ficheiro `.env`** a partir do exemplo e mudar a password:

```
copy .env.example .env
```

(em macOS/Linux: `cp .env.example .env`)

O `.env` guarda as credenciais da base de dados e **nunca vai para o Git** (está no `.gitignore`).

**3. Ligar a base de dados** (precisa do Docker Desktop aberto):

```
docker compose up -d db
```

Para a desligar: `docker compose stop db`. Os dados ficam guardados no volume `pgdata` e não se perdem ao desligar.

As tabelas são criadas automaticamente na primeira vez que um script corre, a partir de [database/schema.sql](../../database/schema.sql).

---

## Resumo dos comandos

| O que quero fazer | Comando | Precisa da base de dados? |
|---|---|---|
| Prever a produção dos próximos 7 dias | `python -m api.weather.solar_forecast` | Sim |
| Atualizar o histórico do tempo | `python -m api.weather.weather_history` | Sim |
| Buscar o histórico de um intervalo de datas | `python -m api.weather.weather_history --start 2024-01-01 --end 2024-12-31` | Sim |
| Ver o gráfico de amanhã | `python -m api.weather.plot_tomorrow` | Não |
| Comparar previsão vs produzido de ontem | `python -m api.weather.forecast_check` | Sim |
| Comparar previsão vs produzido de um dia | `python -m api.weather.forecast_check --date 2026-10-26` | Sim |
| Reconstruir um domingo passado e ver a semana | `python -m api.weather.forecast_check --replay 2026-10-11` | Sim |
| Correr os testes | `python -m pytest api/weather/tests` | Só alguns testes |

Todas as datas estão no formato `AAAA-MM-DD` e em **UTC**.

Os scripts terminam com código `0` se tudo correu bem e `1` se alguma instalação falhou. Assim, um agendador (como o Agendador de Tarefas do Windows ou o `cron`) consegue detetar falhas. Se uma instalação falhar, as outras continuam.

---

## Ficheiros de configuração

Estão em [configs/](configs/).

### [installations.yaml](configs/installations.yaml) — as instalações solares

Cada entrada é uma casa com painéis:

```yaml
installations:
  - user_id: demo_coimbra   # identificador da instalação
    latitude: 40.2069
    longitude: -8.42
    tilt: 30                # inclinação: 0 = painel deitado, 90 = vertical
    azimuth: 0              # orientação (ver nota abaixo)
    kwp: 3.5                # potência de pico instalada, em kW
```

> **Atenção ao `azimuth`:** segue a convenção da Open-Meteo, **não** a da bússola. `0` = sul, `-90` = este, `90` = oeste.

Para acrescentar uma instalação, basta acrescentar uma entrada. Todos os scripts correm para todas as instalações da lista.

### [open_meteo.yaml](configs/open_meteo.yaml) — definições da Open-Meteo e do modelo

| Chave | Para que serve |
|---|---|
| `url`, `hourly` | API de previsão e variáveis pedidas (temperatura, radiação no painel, vento) |
| `forecast_days` | quantos dias completos a previsão cobre (7) |
| `model_version` | nome da versão da fórmula; **mudar sempre que se mudar a fórmula ou uma constante** |
| `constants` | constantes do modelo dos painéis (`noct`, `gamma`, `pr`; ver abaixo) |
| `check` | definições do `forecast_check.py` (ruído de 10%, API das previsões antigas) |
| `history` | definições do `weather_history.py` (API de arquivo, modelo ERA5, atraso, data inicial, variáveis) |

> Se acrescentares uma variável a `history.hourly`, tens de acrescentar também a coluna à tabela `weather_history` em [database/schema.sql](../../database/schema.sql). Se não o fizeres, o script recusa-se a correr e diz qual coluna falta.

---

## solar_forecast.py — previsão da produção solar

```
python -m api.weather.solar_forecast
```

É o script principal. Segue as quatro etapas clássicas de um *pipeline* de dados (ETL + validação), e cada uma é uma secção do ficheiro:

| Etapa | Função | O que faz |
|---|---|---|
| Extract | `fetch_forecast` | pede à Open-Meteo a previsão hora a hora |
| Transform | `estimate_energy` | converte o tempo em energia (kWh) |
| Validate | `validate` | verifica se os dados fazem sentido |
| Load | `insert_forecast` | guarda na tabela `solar_forecast` |

### 1. Extract — buscar a previsão

Para cada instalação, pede à Open-Meteo três variáveis hora a hora:

- `gti` (*global tilted irradiance*): a radiação solar que chega **ao plano do painel**, em W/m². A Open-Meteo já a calcula para a inclinação e orientação de cada instalação.
- `temperature_2m`: temperatura do ar, em °C.
- `wind_speed_10m`: vento, em km/h (é guardado, mas a fórmula não o usa).

A Open-Meteo começa sempre a previsão às 00:00 UTC de hoje, por isso inclui horas que já passaram. A função `next_hours` fica só com as horas **depois do momento em que o script correu**, até às 23:00 UTC daqui a 7 dias.

> Exemplo: se correr num **domingo**, a previsão cobre o resto de domingo e depois **segunda 00:00 até ao domingo seguinte 23:00**.

Os pedidos ficam em cache durante 1 hora (`.cache.sqlite`) e são repetidos até 5 vezes se a ligação falhar.

### 2. Transform — do tempo à energia

O modelo é físico e simples. Para cada hora:

1. **Temperatura da célula:** ao sol, os painéis aquecem mais do que o ar.
   `T_célula = T_ar + (NOCT − 20) / 800 × gti`
2. **Perda por calor:** um painel quente produz menos (`gamma` é negativo, −0,4% por °C acima de 25 °C).
   `fator_temperatura = 1 + gamma × (T_célula − 25)`
3. **Energia:**
   `energia_kWh = kWp × (gti / 1000) × fator_temperatura × PR`

| Constante | Valor | Significado |
|---|---|---|
| `noct` | 45 °C | temperatura nominal de funcionamento da célula |
| `gamma` | −0,004 | perda de potência por °C acima de 25 °C |
| `pr` | 0,80 | *performance ratio*: perdas no inversor, cabos e sujidade |

Como `gti` é a média da radiação ao longo da hora, a potência média em kW nessa hora é igual à energia em kWh. Valores negativos são cortados para 0.

### 3. Validate — verificações

A previsão é rejeitada (e **não é guardada**) se:

- faltar alguma hora ou as horas não forem seguidas;
- houver valores em falta;
- `gti` for negativo;
- a energia de uma hora for negativa ou maior que `1,2 × kWp` (um pouco acima do pico, porque em horas frias e muito soalheiras o painel pode passar ligeiramente a potência nominal);
- não cobrir exatamente da hora seguinte até ao fim do 7.º dia.

### 4. Load — guardar

Grava tudo na tabela `solar_forecast` **numa só transação**: ou fica a previsão toda guardada, ou nada.

- **Cada execução fica guardada** e não apaga as anteriores. É identificada por `forecast_run_at` (a hora a que o script correu).
- `lead_days` diz a quantos dias de distância está cada hora: 0 = dia em que correu, 1 = dia seguinte (D+1), e assim por diante.
- `source` = `live` (as previsões reconstruídas pelo `forecast_check --replay` têm `replay`).

**Exemplo de saída:**

```
demo_coimbra: 98.4 kWh forecast, 180 rows saved to solar_forecast
```

---

## weather_history.py — histórico do tempo

```
python -m api.weather.weather_history
python -m api.weather.weather_history --start 2024-01-01 --end 2024-12-31
```

Guarda o tempo que **realmente fez**, hora a hora, para cada instalação. Os dados vêm da **reanálise ERA5** (arquivo da Open-Meteo, modelo `era5_seamless`). Comparado com as previsões guardadas, mostra quanto a previsão se afastou da realidade.

Guarda 23 variáveis: temperatura, humidade, vários tipos de radiação (média da hora e valor instantâneo), nuvens a várias alturas, vento a 10/80/100/120 m e precipitação.

### Que dias vai buscar

- **Primeira execução:** tudo desde `start_date` (2025-01-01).
- **Execuções seguintes:** vê qual é a última hora guardada e vai buscar só os dias que faltam. Se um dia falhou ou o script não correu, esse dia é apanhado na vez seguinte.
- **Atraso:** a ERA5 só é publicada uns 6 dias depois, por isso o histórico vai só até **hoje − 7 dias** (`lag_days`).
- Os pedidos são divididos **por ano civil**, para nunca pedir mais de um ano de cada vez. São feitos do mais antigo para o mais recente. Se um falhar, a próxima execução retoma a partir dele.

### Com `--start` e/ou `--end`

Vai buscar um intervalo à escolha, por exemplo para carregar dias anteriores a `start_date` ou voltar a buscar dias.

- Sem `--start` → começa em `start_date`.
- Sem `--end` → acaba em hoje − 7 dias.
- Um `--end` mais recente do que hoje − 7 dias é **recusado** (a reanálise ainda não está completa).
- As horas que já existiam nesse intervalo são **substituídas** pelos novos valores, sem duplicados.

### Verificações antes de guardar

- tem exatamente 24 linhas por dia, da 00:00 do primeiro dia às 23:00 do último;
- horas seguidas e sem valores em falta (se falta a última hora, avisa que a reanálise pode ainda não estar publicada);
- nuvens e humidade entre 0 e 100%;
- radiação, vento e precipitação não negativos.

**Exemplo de saída:**

```
demo_coimbra: 2025-01-01 to 2025-12-31, 8760 rows saved
demo_coimbra: 2026-01-01 to 2026-10-20, 7056 rows saved
```

ou, se já estiver tudo:

```
demo_coimbra: up to date (last day 2026-10-20)
```

---

## plot_tomorrow.py — gráfico de amanhã

```
python -m api.weather.plot_tomorrow
```

Desenha um **gráfico de barras** com a energia prevista para cada hora de **amanhã**, uma imagem por instalação.

- Reaproveita as etapas *Extract* e *Transform* do `solar_forecast.py`: vai buscar a previsão à Open-Meteo **na hora** e calcula a energia.
- **Não usa a base de dados** (não precisa do Docker ligado) e **não guarda** nada na base de dados.
- "Amanhã" é na **hora de Lisboa**, das 00:00 às 23:00. Funciona bem nos dias em que a hora muda (dias com 23 ou 25 horas).
- O título mostra a instalação, o kWp e a data. O subtítulo mostra o **total do dia** e a **hora de pico**.
- Guarda a imagem em `data/plots/<user_id>_<AAAA-MM-DD>.png` e abre-a numa janela.

**Exemplo de saída:**

```
demo_coimbra: 14.2 kWh tomorrow, chart saved to data\plots\demo_coimbra_2026-10-28.png
```

---

## forecast_check.py — previsão vs produzido

```
python -m api.weather.forecast_check                      # ontem (UTC)
python -m api.weather.forecast_check --date 2026-10-26    # um dia à escolha
python -m api.weather.forecast_check --replay 2026-10-11  # um domingo passado: a semana toda
```

Serve para avaliar se a previsão acerta. `--date` e `--replay` não podem ser usados juntos.

### A ideia: a previsão de domingo

As semanas vão de **segunda a domingo (UTC)**. Cada dia é comparado com a **previsão feita no domingo anterior** a essa semana:

| Dia verificado | Previsão usada | Antecedência (`lead_days`) |
|---|---|---|
| segunda | domingo anterior | 1 dia |
| terça | o mesmo domingo | 2 dias |
| ... | ... | ... |
| domingo | o mesmo domingo | 7 dias |

Por isso, **é preciso ter corrido o `solar_forecast.py` no domingo** (ou usar `--replay`). Se houver várias execuções nesse domingo, usa a mais recente. Prefere sempre uma execução `live` a uma `replay`.

### O que é o "produzido"

**Não existe contador real**, por isso o "produzido" é **simulado**:

```
produzido = previsão de domingo × (1 + ruído),   ruído ~ Normal(0, 10%)
```

- O ruído é sorteado de forma independente para cada hora e nunca deixa o valor ficar negativo.
- O ruído depende da instalação e do dia (semente fixa). Por isso, **verificar o mesmo dia outra vez dá exatamente os mesmos valores**.
- A radiação (`gti`) recebe o mesmo fator que a energia. A temperatura e o vento ficam iguais à previsão.
- O desvio do ruído está em `check.noise_std` no `open_meteo.yaml`.

### Passos para cada instalação e dia

1. procura a previsão de domingo desse dia na tabela `solar_forecast` (tem de ter as 24 horas);
2. simula o "produzido";
3. verifica os valores (as mesmas regras do `solar_forecast.py`);
4. guarda na tabela `solar_actual` (correr de novo **substitui** e não duplica);
5. desenha um **gráfico de linhas**: previsão (azul, tracejado) vs produzido (laranja, contínuo), com os totais e a diferença em %;
6. guarda a imagem em `data/plots/<user_id>_<AAAA-MM-DD>_check.png`.

O dia tem de **já ter terminado** (em UTC). Hoje é recusado.

**Exemplo de saída:**

```
demo_coimbra 2026-10-26: forecast 12.3 kWh, produced 11.2 kWh (-9%), chart data\plots\demo_coimbra_2026-10-26_check.png
```

### `--replay`: ver uma semana passada sem esperar

Útil quando não se correu o `solar_forecast.py` naquele domingo. Dá-se a data de um **domingo passado**, e a semana seguinte tem de já ter terminado.

1. Vai à **Previous Runs API** da Open-Meteo, que guarda o que as previsões antigas diziam. Para o dia `domingo + k`, usa o que a previsão dizia **k dias antes**. É muito próximo da previsão desse domingo, mas não exatamente igual.
2. Calcula a energia, verifica e guarda em `solar_forecast` com `source = 'replay'`. Um replay anterior do mesmo domingo é substituído. **As previsões `live` nunca são tocadas.**
3. Verifica os 7 dias da semana (segunda a domingo), como acima.

Se a previsão antiga vier com buracos (acontece às vezes a 7 dias), é rejeitada e não é guardada.

---

## db.py — ligação à base de dados

Não se corre diretamente. É usado pelos outros scripts.

| Função | O que faz |
|---|---|
| `connect` | liga ao PostgreSQL usando `DATABASE_URL` (do `.env`) |
| `ensure_schema` | cria as tabelas que faltam, correndo `database/schema.sql` (seguro de repetir) |
| `check_columns` | confirma que as colunas necessárias existem na tabela |
| `open_database` | faz os três passos anteriores e, se algo falhar, explica numa linha porquê |
| `write_rows` | grava várias linhas numa só transação; com `conflict`, substitui as linhas que já existem |

Os scripts verificam a base de dados **antes** de pedir dados à API. Assim, se a base de dados estiver desligada, falham logo, sem gastar pedidos.

---

## Tabelas da base de dados

Todas as horas estão em **UTC**.

| Tabela | Escrita por | Uma linha por | Chave |
|---|---|---|---|
| `solar_forecast` | `solar_forecast.py`, `forecast_check.py --replay` | instalação × execução × hora | `user_id, forecast_run_at, time` |
| `weather_history` | `weather_history.py` | instalação × hora | `user_id, time` |
| `solar_actual` | `forecast_check.py` | instalação × hora | `user_id, time` |

A base de dados também tem regras (`CHECK`) iguais às verificações em Python, por exemplo energia ≥ 0 e nuvens entre 0 e 100. Assim, nenhum valor errado entra por outro caminho.

Para espreitar os dados:

```
docker compose exec db psql -U solarcast -d solarcast
```

```sql
SELECT forecast_run_at, count(*), round(sum(energy_kwh)::numeric, 1) AS kwh
FROM solar_forecast GROUP BY forecast_run_at ORDER BY forecast_run_at DESC;
```

(`solarcast` é o utilizador e a base de dados do `.env.example`. Usa os teus, se os mudaste.)

---

## Testes

```
python -m pytest api/weather/tests
```

| Ficheiro | Testa |
|---|---|
| `test_pv_model.py` | a fórmula da energia |
| `test_forecast_window.py` | que horas a previsão cobre |
| `test_validate.py` | as verificações da previsão |
| `test_store.py` | a gravação em `solar_forecast` |
| `test_history.py` | os dias a buscar e as verificações do histórico |
| `test_plot_tomorrow.py` | a escolha das horas de "amanhã" na hora local |
| `test_forecast_check.py` | a previsão de domingo, o ruído, o replay e a gravação em `solar_actual` |
| `test_db.py` | a ligação, a criação das tabelas e a verificação de colunas |

Os testes que precisam da base de dados **são saltados** (*skipped*) se `TEST_DATABASE_URL` não estiver definido (no `.env`; ver `.env.example`). Quando correm, **desfazem tudo o que escrevem**, por isso podem usar a mesma base de dados dos scripts.

O ficheiro `tests/weather_api.ipynb` é um caderno de exploração da API, não é um teste.

---

## Erros comuns

| Mensagem | Causa | Solução |
|---|---|---|
| `DATABASE_URL is not set` | falta o ficheiro `.env` | `copy .env.example .env` |
| `cannot connect to the database ... Is it running?` | a base de dados está desligada | abrir o Docker Desktop e `docker compose up -d db` |
| `columns missing from table ...` | variável nova no YAML sem coluna na tabela | acrescentar a coluna em `database/schema.sql` |
| `ModuleNotFoundError: No module named 'api'` | o comando não correu a partir de `Projeto/` | `cd` para a pasta `Projeto` e usar `python -m ...` |
| `no Sunday forecast for the week of ...` | o `solar_forecast.py` não correu nesse domingo | usar `--replay <domingo>` |
| `... is not over yet` | o dia (ou a semana do replay) ainda não acabou em UTC | escolher um dia anterior |
| `--end ... is too recent` | a reanálise ERA5 ainda não está publicada | usar uma data até hoje − 7 dias |
