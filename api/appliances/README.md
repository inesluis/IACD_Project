# Catálogo de eletrodomésticos (EPREL)

Este módulo faz duas coisas:

1. **Descarrega o catálogo** de eletrodomésticos da EPREL (a base de dados europeia das etiquetas energéticas) para a tabela `appliance_model` da base de dados PostgreSQL.
2. **Encontra o modelo do utilizador** nesse catálogo: a partir do tipo, da marca e do modelo indicados pelo utilizador, escolhe o modelo do catálogo mais parecido e guarda-o na tabela `user_appliance`.

| Ficheiro | O que faz |
|---|---|
| `eprel_sync.py` | Descarrega os modelos da EPREL e guarda-os em `appliance_model` |
| `match.py` | Normaliza nomes de marcas e modelos, encontra o modelo mais parecido e gere os eletrodomésticos de cada utilizador |
| `configs/eprel.yaml` | Tipos de eletrodomésticos, grupos EPREL a descarregar e de que campo vem cada coluna |
| `tests/` | Testes automáticos |

Todos os comandos correm a partir da pasta `Projeto/`.

---

## 1. Preparação

### Base de dados

As tabelas ficam na mesma base de dados PostgreSQL dos pipelines de meteorologia. Para a arrancar:

    docker compose up -d db

As tabelas `appliance_model` e `user_appliance` estão definidas em `database/schema.sql` e são criadas automaticamente na primeira vez que um dos scripts corre.

### Chave da API EPREL

O download precisa de uma chave da API pública da EPREL. Coloque-a no ficheiro `.env` (que não vai para o git):

    EPREL_API_KEY=<a sua chave>

A chave nunca é escrita no código nem nos registos. A EPREL limita cada chave a **5 pedidos por segundo**; o script usa no máximo 4.

---

## 2. Comandos

### Descarregar o catálogo

    python -m api.appliances.eprel_sync                          # todos os 19 grupos (cerca de 40 minutos)
    python -m api.appliances.eprel_sync --type maq_lavar         # só um tipo de eletrodoméstico
    python -m api.appliances.eprel_sync --type maq_lavar --type maq_secar   # vários tipos
    python -m api.appliances.eprel_sync --group ovens            # só um grupo EPREL
    python -m api.appliances.eprel_sync --group ovens --group rangehoods    # vários grupos

`--type` e `--group` podem repetir-se e combinar-se. Sem nenhum dos dois, descarrega tudo. Um nome desconhecido faz o script parar logo e mostrar a lista de nomes válidos.

#### Opções de `--type` e `--group`

Há 13 tipos e 19 grupos. Cada tipo agrupa um ou mais grupos EPREL: `--type` descarrega todos os grupos do tipo, `--group` só esse grupo.

| `--type` | Eletrodoméstico | `--group` incluídos |
|---|---|---|
| `maq_lavar` | máquina de lavar roupa | `washingmachines2019`, `washingmachines` |
| `maq_secar` | máquina de secar roupa | `tumbledryers20232534`, `tumbledriers` |
| `maq_loica` | máquina da loiça | `dishwashers2019`, `dishwashers` |
| `maq_lavar_secar` | máquina de lavar e secar | `washerdriers2019`, `washerdriers` |
| `frigorifico` | frigorífico, congelador | `refrigeratingappliances2019`, `refrigeratingappliances` |
| `forno` | forno | `ovens` |
| `exaustor` | exaustor | `rangehoods` |
| `ecra` | televisão, monitor | `electronicdisplays`, `televisions` |
| `ar_condicionado` | ar condicionado | `airconditioners` |
| `termoacumulador` | aquecedor de água | `waterheaters` |
| `deposito_agua_quente` | depósito de água quente | `hotwaterstoragetanks` |
| `aquecedor` | aquecedor ambiente | `localspaceheaters` |
| `lampada` | lâmpada (etiqueta antiga) | `lamps` |

Quando um tipo tem dois grupos, o primeiro é o da etiqueta atual (A a G) e o segundo o da etiqueta antiga (A+++ a D). Por exemplo, `--type maq_lavar` descarrega os dois, e `--group washingmachines2019` só o da etiqueta atual. A lista vem de `configs/eprel.yaml`: um grupo acrescentado lá passa a ser uma opção válida (ver secção 4). O número de modelos de cada grupo está na secção 4.

No fim de cada grupo aparece uma linha como:

    washingmachines2019: read 22746, saved 22746, skipped 0
      missing: energy_kwh_cycle=12, programme_minutes=3

- **read**: registos recebidos da EPREL.
- **saved**: registos guardados (novos ou atualizados).
- **skipped**: registos ignorados (ver secção 3.4).
- **missing**: quantos registos não tinham cada um dos campos numéricos. É normal haver alguns; se um campo faltar na maioria dos registos de um grupo, o nome do campo em `eprel.yaml` está provavelmente errado (ver secção 6).

Código de saída: `0` se tudo correu bem, `1` se algum grupo falhou ou faltar a chave/base de dados, `2` se um nome de tipo ou grupo for inválido.

### Encontrar o modelo de um utilizador

Só procurar (não guarda nada):

    python -m api.appliances.match --type maq_lavar --brand "LG Electronics" --model "F4WV308S4"

Procurar e guardar para um utilizador:

    python -m api.appliances.match --user demo_coimbra --type ecra --brand "Samsung" --model "UE55TU7172" --add
    python -m api.appliances.match --user demo_coimbra --type lampada --brand "Philips" --model "8718699" --add --quantity 6

| Opção | Obrigatória | Significado |
|---|---|---|
| `--type` | sim | tipo de eletrodoméstico (tabela da secção 4) |
| `--brand` | sim | marca, tal como o utilizador a escreveu |
| `--model` | sim | modelo, tal como o utilizador o escreveu |
| `--user` | com `--add` | identificador do utilizador (o mesmo `user_id` das tabelas de meteorologia) |
| `--add` | não | guarda o resultado em `user_appliance` |
| `--quantity` | não | número de unidades iguais (por defeito 1) |

Exemplo de resultado:

    close (0.93): LG Electronics F4WV308S4W [washingmachines2019, reg 352460, class A]
    other candidates:
      0.88  F4WV308S4E  [washingmachines2019, reg 352501]
    saved for demo_coimbra (id 12)

Quando a marca não existe para esse tipo:

    none: no maq_lavar models of brand 'LG' in the catalogue
    did you mean: LG Electronics

### Testes

    python -m pytest api/appliances/tests

Os testes que usam a base de dados são saltados se `TEST_DATABASE_URL` não estiver definido no `.env`. Tudo o que escrevem é desfeito no fim de cada teste, e usam marcas inventadas e números de registo acima de 9 000 000 000, por isso não interferem com o catálogo real (a base de dados de teste é a mesma da aplicação).

Não use `python -m pytest api`: os dois scripts `test_api*.py` da pasta `api/eletrodomestic/` não são testes e fazem a recolha falhar.

---

## 3. Como funciona o download (`eprel_sync.py`)

O script segue as mesmas fases dos pipelines de meteorologia: Extract, Transform, Validate, Load.

### 3.1 Extract: pedir as páginas à EPREL

Para cada grupo, pede `GET https://eprel.ec.europa.eu/api/products/<grupo>?_page=N&_limit=100`, com a chave no cabeçalho `X-API-KEY`.

- Cada página traz no máximo **100 modelos** (o máximo da API).
- A página 1 diz quantos modelos o grupo tem (`size`); daí sai o número de páginas.
- As restantes páginas são pedidas **4 de cada vez**, e no máximo **4 pedidos começam por segundo** (classe `RateLimiter`). Como cada página demora cerca de 2,6 s a chegar, isto reduz o download completo de cerca de 2,5 horas (uma página de cada vez) para cerca de 40 minutos, sem ultrapassar o limite da EPREL.
- **Repetições**: se a EPREL responder 429 (demasiados pedidos) ou 5xx, se a ligação falhar, se a resposta vier cortada ou não for JSON válido, o pedido é repetido até 4 vezes, esperando 2, 4, 8 e 16 segundos. As repetições também passam pelo limitador.
- **Chave recusada** (401/403): o script para imediatamente com uma mensagem sobre `EPREL_API_KEY`.

### 3.2 Transform: de registo EPREL a linha da tabela

Cada registo é transformado numa linha de `appliance_model` (função `to_row`):

- **Identidade**: `registration_number` (número de registo EPREL, único em todos os grupos), `product_group`, `appliance_type`.
- **Marca e modelo**: guardados tal como vêm (`brand`, `model_identifier`) e normalizados (`brand_key`, `model_key`, ver secção 5.1).
- **Colunas numéricas** (`energy_kwh_cycle`, `programme_minutes`, `energy_kwh_annual`, `power_on_w`, `capacity`): lidas do campo indicado em `eprel.yaml` para esse grupo. Um valor em falta, nulo, negativo, infinito, que não seja número ou demasiado grande fica **vazio (NULL)**, nunca faz o script parar. Números enviados como texto (`"8"`) são convertidos.
- **Datas** (`on_market_start`, `on_market_end`): a EPREL envia `[ano, mês, dia]`. O ano 9999, que a EPREL usa para "sem data de fim", fica vazio.
- **`raw`**: o registo EPREL completo, sem alterações, em JSON. Assim nenhuma informação se perde e qualquer campo pode ser usado mais tarde, por exemplo `raw->>'noise'`.

### 3.3 Validate

- Um registo que não seja um objeto JSON, ou a que falte `eprelRegistrationNumber`, `modelIdentifier` ou `supplierOrTrademark`, é ignorado e contado em **skipped**.
- Para cada coluna numérica, conta-se quantos registos não a tinham. É isso que aparece na linha **missing**.

### 3.4 Load: guardar

- Cada página é guardada **assim que chega**. Se o script for interrompido, o que já foi guardado fica.
- A gravação é um *upsert* pelo `registration_number`: correr de novo **atualiza** os modelos existentes (incluindo `raw` e `fetched_at`) em vez de os duplicar.
- Se a base de dados recusar uma página, essa página é gravada linha a linha. As linhas recusadas contam como **skipped** e as outras são guardadas.
- Se um grupo falhar de vez (rede ou base de dados), aparece `FAILED` nesse grupo e o script **continua com o grupo seguinte**. Basta voltar a correr esse grupo com `--group`.
- Modelos que deixem de aparecer na EPREL **não são apagados**, porque um utilizador pode estar associado a eles.

---

## 4. Tipos e grupos (`configs/eprel.yaml`)

Os nomes dos tipos seguem os da simulação de consumos (`maq_lavar`, `maq_secar`, `maq_loica`), para que um eletrodoméstico se possa ligar diretamente a `eventos.csv` mais tarde. Dentro de cada tipo, os grupos estão por ordem do **regulamento mais recente para o mais antigo**. Os grupos antigos estão incluídos para se poderem encontrar eletrodomésticos vendidos antes das etiquetas atuais.

| Tipo | Eletrodoméstico | Grupos EPREL (mais recente primeiro) | Modelos (out. 2026) |
|---|---|---|---|
| `maq_lavar` | máquina de lavar roupa | `washingmachines2019`, `washingmachines` | 22 746 + 8 335 |
| `maq_secar` | máquina de secar roupa | `tumbledryers20232534`, `tumbledriers` | 6 123 + 5 521 |
| `maq_loica` | máquina da loiça | `dishwashers2019`, `dishwashers` | 14 700 + 7 122 |
| `maq_lavar_secar` | máquina de lavar e secar | `washerdriers2019`, `washerdriers` | 3 421 + 1 214 |
| `frigorifico` | frigorífico, congelador | `refrigeratingappliances2019`, `refrigeratingappliances` | 56 529 + 16 836 |
| `forno` | forno | `ovens` | 27 116 |
| `exaustor` | exaustor | `rangehoods` | 24 197 |
| `ecra` | televisão, monitor | `electronicdisplays`, `televisions` | 37 634 + 8 424 |
| `ar_condicionado` | ar condicionado | `airconditioners` | 33 032 |
| `termoacumulador` | aquecedor de água | `waterheaters` | 12 598 |
| `deposito_agua_quente` | depósito de água quente | `hotwaterstoragetanks` | 7 459 |
| `aquecedor` | aquecedor ambiente | `localspaceheaters` | 23 441 |
| `lampada` | lâmpada (etiqueta antiga) | `lamps` | 36 192 |

No total são cerca de 352 600 modelos, cerca de 3 530 pedidos e cerca de 1 GB na base de dados, a maior parte na coluna `raw`.

As **fontes de luz** (`lightsources`, 667 672 modelos) ficaram de fora de propósito: multiplicariam várias vezes o tempo de download e o espaço ocupado, e raramente alguém sabe o modelo das suas lâmpadas.

### Formato do ficheiro

```yaml
types:
  forno:                     # tipo de eletrodoméstico
    groups:
      ovens:                 # grupo EPREL (url_code)
        energy_kwh_cycle:    # coluna <- caminho no registo EPREL
          - cavities[0].energyConsumptionCycle
          - cavities[0].energyConsumptionCycleFanForced
        capacity: cavities[0].volume
        capacity_unit: litres  # texto fixo, não é um caminho
```

- `a.b` é o campo `b` dentro do objeto `a`; `a[0].b` é o campo `b` do primeiro elemento da lista `a`.
- Uma lista de caminhos é tentada por ordem e fica o primeiro valor não nulo.
- Colunas que não aparecem num grupo ficam vazias.
- `capacity_unit` pode ser `kg`, `place_settings` (serviços, nas máquinas da loiça), `litres`, `inches` ou `kw`.

Algumas escolhas a ter em conta ao usar os dados:

- **Fornos**: só se lê a primeira cavidade. Fornos a gás ficam sem `energy_kwh_cycle`.
- **Ar condicionado**: `energy_kwh_annual` é o consumo em arrefecimento; o de aquecimento depende da zona climática e está em `raw`.
- **Lâmpadas**: `weightedEnergyConsumption` vem em kWh por 1000 h, o que equivale à potência média em W, e por isso vai para `power_on_w`.
- **Aquecedores ambiente** incluem salamandras a lenha e pellets; **aquecedores de água** incluem bombas de calor e modelos a gás.

### Acrescentar um grupo

1. Ver um registo do grupo, por exemplo com uma página de 1 modelo da API, e escolher os campos.
2. Acrescentá-lo em `eprel.yaml`, dentro do tipo certo, pela ordem do regulamento.
3. Se for um **tipo novo**, acrescentar também o nome às duas listas `CHECK (appliance_type IN (...))` em `database/schema.sql`. Numa base de dados que já tem as tabelas é preciso alterar a restrição à mão, porque `CREATE TABLE IF NOT EXISTS` não a atualiza.
4. Correr `python -m api.appliances.eprel_sync --group <grupo>` e ver a linha `missing`.

---

## 5. Como funciona a correspondência (`match.py`)

### 5.1 Normalização dos nomes

Para que grafias diferentes coincidam, marca e modelo são normalizados dos dois lados: no catálogo, quando é descarregado, e no texto do utilizador.

- **Modelo** (`normalize_model`): maiúsculas, só letras e algarismos.
  `"F4WV-308 s4w"`, `"f4wv308s4w"` e `" F4WV.308/S4W "` dão todos `F4WV308S4W`.
- **Marca** (`normalize_brand`): minúsculas, sem acentos nem pontuação, e sem palavras societárias no fim (`inc`, `ltd`, `gmbh`, `sa`, `sl`, `srl`, `spa`, `ag`, `bv`, `co`, `corp`, `corporation`, `limited`).
  `"LG Electronics"`, `"LG Electronics Inc."` e `"LG ELECTRONICS GmbH"` dão todos `lg electronics`; `"Fagor S.A."` dá `fagor`; `"Électrolux"` dá `electrolux`.

### 5.2 Escolha do modelo (`find_match`)

1. **Candidatos**: todos os modelos do tipo pedido cuja marca normalizada é igual à do utilizador, em todos os grupos desse tipo (antigos e novos).
2. **Sem candidatos**: o resultado é `none` e são sugeridas até 5 marcas: primeiro as que começam pelas palavras escritas ("LG" sugere "LG Electronics"), depois as de grafia parecida ("Samsumg" sugere "Samsung").
3. **Pontuação** de cada candidato, de 0 a 1:
   - 1 se o modelo normalizado é igual;
   - senão, a semelhança entre os dois textos (`difflib.SequenceMatcher`);
   - se um começa pelo outro e o mais curto tem pelo menos 5 caracteres, a pontuação é pelo menos 0,9. Isto cobre sufixos como `WW90T534DAW` e `WW90T534DAW/S3`.
4. **Vencedor**: a pontuação mais alta. Em caso de empate, ganha o grupo de regulamento mais recente e depois o modelo colocado no mercado mais tarde. Os 4 seguintes são devolvidos como candidatos alternativos.
5. **Classificação** do resultado:

| Resultado | Pontuação | Significado |
|---|---|---|
| `exact` | 1 | o mesmo modelo |
| `close` | ≥ 0,8 | nome muito parecido, por exemplo sem sufixo |
| `weak` | < 0,8 | o modelo mais parecido daquela marca, mas pouco parecido |
| `none` | | não há modelos daquela marca e tipo |

O melhor modelo é **sempre** devolvido, mesmo quando é `weak`. Cabe a quem usa o resultado decidir se confia nele; `match_kind` e `score` dizem quanto.

### 5.3 Usar a partir de Python

```python
from api.weather.db import connect
from api.appliances.match import (
    find_match, add_appliance, remove_appliance, list_user_appliances,
    list_brands, list_models,
)

conn = connect()  # usa DATABASE_URL do .env

list_brands(conn, "maq_lavar")              # marcas disponíveis, um nome por marca
list_models(conn, "maq_lavar", "LG Electronics")  # modelos de uma marca, com classe, kWh e capacidade

match = find_match(conn, "maq_lavar", "LG Electronics", "F4WV308S4")
match.match_kind, match.score               # por exemplo ("close", 0.95)
match.model                                 # dict: registration_number, product_group, brand,
                                            #       model_identifier, energy_class, score
match.candidates                            # os 4 seguintes
match.brand_suggestions                     # só quando match_kind == "none"

appliance_id, match = add_appliance(conn, "demo_coimbra", "ecra", "Samsung", "UE55TU7172", quantity=2)
list_user_appliances(conn, "demo_coimbra")  # eletrodomésticos do utilizador, com o modelo associado
remove_appliance(conn, appliance_id)        # True se existia
```

`find_match` e `add_appliance` dão `ValueError` se o tipo não existir, se a marca ou o modelo ficarem vazios depois de normalizados (por exemplo `" - "`), ou se `quantity` for menor que 1.

---

## 6. Tabelas

### `appliance_model`: o catálogo

| Coluna | Conteúdo |
|---|---|
| `registration_number` | número de registo EPREL (chave primária) |
| `product_group`, `appliance_type` | grupo EPREL e tipo de eletrodoméstico |
| `brand`, `model_identifier` | marca e modelo como estão na EPREL |
| `brand_key`, `model_key` | marca e modelo normalizados |
| `energy_class` | classe energética (`APPP` = A+++, `AP` = A+) |
| `energy_kwh_cycle`, `programme_minutes` | energia e duração de um ciclo padrão |
| `energy_kwh_annual` | consumo anual indicado pela EPREL |
| `power_on_w` | potência ligado (W) |
| `capacity`, `capacity_unit` | tamanho e unidade |
| `on_market_start`, `on_market_end` | datas de colocação e saída do mercado |
| `raw` | registo EPREL completo (JSON) |
| `fetched_at` | quando foi descarregado |

Para verificar um campo que falta em muitos registos:

```sql
SELECT raw FROM appliance_model
WHERE product_group = 'washingmachines' AND programme_minutes IS NULL
LIMIT 3;
```

### `user_appliance`: os eletrodomésticos de cada utilizador

Um utilizador pode ter **vários eletrodomésticos do mesmo tipo** (duas televisões, um aquecedor por divisão). Cada chamada a `add_appliance` acrescenta uma linha.

| Coluna | Conteúdo |
|---|---|
| `id` | identificador da linha |
| `user_id`, `appliance_type` | utilizador e tipo |
| `input_brand`, `input_model` | o que o utilizador escreveu, sem alterações |
| `registration_number` | modelo escolhido no catálogo (vazio quando o resultado é `none`) |
| `match_kind`, `score` | confiança na correspondência |
| `quantity` | número de unidades iguais |
| `matched_at` | quando foi guardado |

---

## 7. Problemas comuns

| Mensagem | Causa e solução |
|---|---|
| `FAILED - EPREL_API_KEY is not set` | falta a chave no `.env` |
| `FAILED - EPREL refused the key (HTTP 403)` | chave errada ou suspensa |
| `FAILED - cannot connect to the database` | a base de dados não está a correr: `docker compose up -d db` |
| `<grupo>: FAILED - ...` | o grupo falhou depois das repetições; voltar a correr com `--group <grupo>` |
| `none: no ... models of brand ...` | marca escrita de outra forma (ver as sugestões) ou o catálogo desse tipo ainda não foi descarregado |
| `FAILED - model is empty` | o modelo só tinha espaços ou pontuação |

### Limitações conhecidas

- Quando dois modelos empatam em tudo (pontuação, grupo e data), o escolhido pode variar de uma execução para outra.
- Um prefixo curto como `WW90T` é classificado como `close`, mesmo quando muitos modelos começam por ele.
- O comando `match` precisa da base de dados a correr mesmo para mostrar o erro de um `--type` inválido.
- `--quantity` igual ou superior a 32 768 dá um erro Python.
- Se o catálogo nunca foi descarregado, a mensagem diz que a marca não tem modelos em vez de dizer que o catálogo está vazio.
