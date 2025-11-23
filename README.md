# 🎓 Academic Bot (Microservices Architecture)

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/RabbitMQ-FF6600?style=for-the-badge&logo=rabbitmq&logoColor=white)
![MongoDB](https://img.shields.io/badge/MongoDB-47A248?style=for-the-badge&logo=mongodb&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white)

<!-- Cole isso logo abaixo dos badges de tecnologia existentes -->
<div align="left">

[![Telegram](https://img.shields.io/badge/Telegram-Iniciar_Bot-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/GustavosArchBotdo )
[![Discord](https://img.shields.io/badge/Discord-Adicionar_ao_Servidor-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1442108465546788884)
[![Falar com o Bot](https://img.shields.io/badge/Discord-DM_Direta-5865F2?style=for-the-badge&logo=discord&logoColor=white)](https://discord.com/users/1442129543589658678)
</div>


Um assistente acadêmico robusto e escalável construído sobre uma arquitetura de microsserviços. O sistema gerencia tarefas, provas e trabalhos através de uma interface CLI via Telegram (e extensível para outras plataformas), com foco em produtividade, notificações inteligentes e alta disponibilidade.

---

## 🏗️ Arquitetura

O projeto utiliza uma arquitetura orientada a eventos para garantir performance e desacoplamento:

| Serviço | Função |
| :--- | :--- |
| **API (Master)** | Gateway FastAPI com Anti-Spam e Rate Limiting. Recebe Webhooks. |
| **RabbitMQ** | Broker de mensageria que enfileira as requisições para processamento assíncrono. |
| **Worker** | Consumidor principal. Processa lógica de negócios (CRUD, CLI parsing) e interage com o Telegram. |
| **Notifier** | Serviço de loop temporal. Monitora prazos e envia alertas (Smart/Manual). |
| **MongoDB** | Banco de dados NoSQL para persistência flexível de eventos. |
| **MinIO (S3)** | Object Storage compatível com S3. Configurado para armazenamento futuro de anexos e backups. |
| **Observability** | Prometheus e Grafana para monitoramento de métricas e saúde dos containers. |

---

## 🚀 Instalação e Configuração

### 1. Clone o repositório
```bash
git clone https://github.com/GustavoBorges13/academic-bot.git
cd academic-bot
```

### 2. Variáveis de Ambiente (.env)
Crie um arquivo `.env` na raiz. **Este arquivo contém segredos e não deve ser commitado.**

```bash
# .env

# --- TELEGRAM & API ---
TELEGRAM_TOKEN=seu_token_aqui
# Token secreto para validar que a requisição veio mesmo do Telegram (crie uma string aleatória)
TG_WEBHOOK_SECRET=sua_string_secreta_webhook
# URL pública onde o Telegram vai bater (necessário para Webhooks, ex: via Cloudflare Tunnel)
API_PUBLIC_URL=https://sua-url-publica.com
# Chave Mestra para comandos de admin (ex: bypass de tempo no alert)
ADMIN_KEY=sua_chave_admin_secreta

# --- MONGO DB ---
MONGO_INITDB_ROOT_USERNAME=admin
MONGO_INITDB_ROOT_PASSWORD=senha_mongo
# Conexão interna do Python
MONGO_URI=mongodb://admin:senha_mongo@mongo:27017/academic_db?authSource=admin

# --- MONGO EXPRESS (GUI) ---
# Conexão da interface gráfica (deve usar as mesmas credenciais do Mongo acima)
MONGO_URI_GUI=mongodb://admin:senha_mongo@mongo:27017/
# Login para acessar o painel web (http://localhost:8081)
MONGO_EXPRESS_USER=admin
MONGO_EXPRESS_PASS=senha_do_site_mongo

# --- RABBIT MQ ---
# Credenciais de criação do RabbitMQ
RABBITMQ_DEFAULT_USER=guest
RABBITMQ_DEFAULT_PASS=senha
# Conexão interna do Python
RABBIT_HOST=rabbitmq
RABBIT_USER=guest
RABBIT_PASS=senha_rabbit_guest

# --- MINIO (S3 Compatible Storage) ---
# Armazenamento de objetos (Futuro: Anexos e Backups)
MINIO_ROOT_USER=admin
MINIO_ROOT_PASSWORD=senha_minio
R2_ENDPOINT=http://minio:9000
R2_ACCESS_KEY=admin
R2_SECRET_KEY=senha_minio
BUCKET_NAME=arquivos-academicos
```

### 3. Execução
```bash
 docker-compose up -d --build
```

---

# 📚 Manual de Referência (CLI)

O bot opera através de um poderoso sistema de **Linha de Comando (CLI)** via chat. Abaixo está a documentação completa de todas as sintaxes suportadas.

> **Nota:** A barra `/` no início dos comandos é opcional.

## 🌱 Adicionar (`add`)

Crie categorias vazias ou eventos completos com metadados.

**Sintaxe Básica:**
*   `add [Categoria]` (Cria pasta vazia)
*   `add [Categoria] [Evento] [Data]`

**Flags de Detalhes:**
*   `-alta`, `-media`, `-baixa` (Define prioridade. Padrão: baixa)
*   `-obs "Texto da observação"` (Adiciona nota)

**Exemplos:**
```bash
add Provas
add Provas Cálculo 10/12
add Trab SO2 15/12 -alta -obs "Fazer o relatório"
```

---

## ✏️ Editar Avançado (`edit`)

O comando mais poderoso do sistema. Utiliza o operador `>` para transformar dados.
**Lógica:** `Origem > Destino`

### 1. Renomear Entidades
Alterar apenas o nome mantendo as outras propriedades.

*   **Categoria:**
    `edit aushuah > complementares`
    *(Renomeia a categoria inteira)*
*   **Evento (Matéria):**
    `edit provas SO2 > provas SO1`
    *(Renomeia o evento 'SO2' para 'SO1' dentro de 'provas')*
*   **Data de um Evento:**
    `edit provas SO2 23/11/2025 > provas SO2 24/11/2025`
    *(Altera a data específica de um item)*

### 2. Mover (Reorganizar)
Mover itens entre categorias ou agrupar datas em outros eventos.

*   **Mover Evento para outra Categoria:**
    `edit provas SO2 > trabalhos SO2`
    *(Move todo o evento SO2 para a categoria Trabalhos)*
    `edit provas SO2 > trabalhos`
    *(Mesma função, sintaxe curta)*
*   **Mover Data Específica:**
    `edit provas SO2 23/11 > provas LFA 23/11`
    *(Tira a entrega do dia 23 de SO2 e joga para LFA)*

### 3. Edição Híbrida (Mover + Renomear)
Faz as duas coisas em um único comando.

*   `edit provas SO2 > trabalhos SO3`
    *(Move de Provas para Trabalhos E renomeia de SO2 para SO3)*
*   `edit provas SO2 23/11 > provas LFA 24/11`
    *(Move o item para LFA E muda a data para o dia 24)*

### 4. Edição em Lote (Flags e Metadados)
Use `>` apontando para flags para atualizar múltiplos itens de uma vez.

*   **Atualizar Evento Todo:**
    `edit trabalhos SO2 > -alta -obs "Urgente"`
    *(Define prioridade Alta e Obs para TODAS as datas de SO2)*
*   **Atualizar Data Específica:**
    `edit trabalhos SO2 23/11 > -media`
    *(Altera prioridade apenas do item do dia 23/11)*

---

## 🗑️ Deletar (`del`)

A deleção funciona em cascata hierárquica.

1.  `del Categoria`
    *   Ex: `del provas` (Apaga a categoria e **tudo** dentro dela)
2.  `del Categoria Evento`
    *   Ex: `del provas SO2` (Apaga o evento SO2 e todas as suas datas)
3.  `del Categoria Evento Data`
    *   Ex: `del provas SO2 23/11` (Apaga apenas o item daquele dia)

---

## 🔔 Notificações & Alertas (`alert`)

O sistema possui um **Notifier** dedicado que verifica prazos e envia alertas proativos.

**Comando:** `alert -f [TEMPO] -mode [MODO]`

### Parâmetros
*   **`-f` (Frequência):** De quanto em quanto tempo o bot vai te notificar.
    *   Formatos: `h` (horas), `m` (minutos), `d` (dias).
    *   Limites: Mínimo `1h`, Máximo `7d`.
    *   Exemplos: `12h`, `1h30m`, `1d`.
*   **`-mode` (Modo de Inteligência):**
    *   `smart` (Padrão): Notifica apenas itens de prioridade Alta/Média OU que vencem em ≤ 30 dias.
    *   `manual`: Envia o relatório completo de toda a agenda.

### Modo Desenvolvedor (Bypass)
Para testes rápidos (ignorando o limite mínimo de 1h), use a chave de admin configurada no `.env`:
`alert -f 10s -K [SUA_ADMIN_KEY]`

**Desativar:**
`alert desativar`

---

## 👁️ Comandos de Visualização

| Comando | Descrição |
| :--- | :--- |
| `list cat` | Lista todas as categorias e contagem de itens. |
| `list event` | Abre o Painel Interativo (Botões). |
| `tree h` | Visualização em Árvore Horizontal. |
| `tree v` | Visualização em Árvore Vertical. |
| `tree notify` | Visualização compacta (formato usado nas notificações). |
| `export` | Gera backup JSON e cria link de API seguro para integração. |
| `menu` | Abre o menu gráfico principal. |
| `ajuda` | Mostra o guia rápido no chat. |

---

Perfeito! Essa funcionalidade é um dos grandes diferenciais do seu bot (transformá-lo em uma **API Headless** para o usuário).

Aqui está a seção dedicada para **API & Exportação**, seguindo o mesmo padrão visual profissional. Adicione isso **antes** da seção "📄 Licença" no seu `README.md`.

---

## 🔗 API & Integrações Externas (`export`)

O comando `/export` não serve apenas para backup. Ele transforma seu bot em um **Servidor de API Pessoal**, permitindo integrar sua agenda acadêmica com Notion, Scriptable (iOS Widget), Home Assistant ou qualquer outra aplicação.

### Como funciona
Ao digitar `/export`, o bot gera:
1.  📄 **Arquivo JSON Físico:** Um snapshot estático dos seus dados atuais (para backup frio).
2.  🔗 **Link Dinâmico (Endpoint):** Uma URL pública e segura contendo seus dados em tempo real.

### Exemplo de Uso
```json
// GET https://api.gustavos.cloud/export/e02cc3d7-ab52-48fa-94eb-b0a76ab46bfax

{
  "status": "success",
  "user_id_hash": "8392819...",
  "generated_at": 1732358400,
  "total_items": 15,
  "data": [
    {
      "materia": "Cálculo II",
      "data": "15/12/2025",
      "tipo": "Provas",
      "prioridade": "critical",
      "observacoes": "Capítulo 4 e 5"
    },
    {
      "materia": "Sistemas Operacionais",
      "data": "20/12/2025",
      "tipo": "Trabalhos",
      "prioridade": "medium"
    }
  ]
}
```

#### Exemplo de Integração (cURL)
```bash
curl -X GET "https://api.gustavos.cloud/export/SEU_TOKEN_AQUI"
```

### 🔐 Segurança & Revogação
O link gerado utiliza um **Token UUID v4**.
*   **Vazamento:** Se você compartilhar o link acidentalmente, clique no botão **"🔄 Revogar Token"** no Telegram.
*   **Efeito:** O link antigo para de funcionar imediatamente (retorna 404/403) e um novo link é gerado para você.


---

## 📄 Licença
Este projeto está sob a licença MIT.
