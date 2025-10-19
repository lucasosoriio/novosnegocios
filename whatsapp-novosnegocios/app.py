from flask import Flask, render_template, request, send_file, jsonify
import pandas as pd
import requests
import os
import re
import json
import time
import threading
from datetime import datetime, date

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = 'uploads'
LOG_DIR = 'logs'
LOG_PATH = os.path.join(LOG_DIR, 'log_envios.csv')

# --------------------------------------------------
# CONFIGURAÇÕES GERAIS DA EVOLUTION
# --------------------------------------------------
SESSION_ID = "novosnegocios"              # nome da sua instância no Evolution
API_BASE = "http://localhost:8081"       # Evolution API porta exposta no Docker
API_KEY = "ABCD"                         # sua chave da Evolution API (.env)
HEADERS = {"apikey": API_KEY}

API_URL_SEND = f"{API_BASE}/message/sendText/{SESSION_ID}"
API_URL_CHECK = f"{API_BASE}/instance/connectionState/{SESSION_ID}"

# --------------------------------------------------
# Mensagem padrão
# --------------------------------------------------
MENSAGEM_PADRAO = """Olá {NOME DO CLIENTE}! Tudo bem? 😊
Aqui é da Estasa Administradora de Condomínios.
Você ainda é síndico(a) do condomínio {NOME DO CONDOMÍNIO} ou tem alguma relação com ele?
Gostaríamos de marcar uma breve conversa para te apresentar a Estasa e nossos diferenciais."""

# --------------------------------------------------
# Estado global de progresso (para AJAX)
# --------------------------------------------------
PROGRESS = {
    "running": False,
    "total": 0,
    "enviados": 0,
    "falhas": 0,
    "pulados": 0,
    "atual": "",
    "mensagem": "",
    "erro": ""
}
CANCELAR = {"flag": False}

# --------------------------------------------------
# Utilidades de log
# --------------------------------------------------
def ensure_log():
    os.makedirs(LOG_DIR, exist_ok=True)
    if not os.path.exists(LOG_PATH):
        pd.DataFrame(columns=[
            "data_hora", "arquivo", "sindico", "condominio", "telefone", "status", "mensagem"
        ]).to_csv(LOG_PATH, index=False, encoding="utf-8-sig")

def append_log(arquivo, sindico, condominio, telefone, status, mensagem):
    ensure_log()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df_row = pd.DataFrame([{
        "data_hora": now,
        "arquivo": arquivo,
        "sindico": sindico,
        "condominio": condominio,
        "telefone": telefone,
        "status": status,
        "mensagem": mensagem
    }])
    df_row.to_csv(LOG_PATH, mode='a', header=not os.path.getsize(LOG_PATH), index=False, encoding="utf-8-sig")

def enviados_hoje():
    ensure_log()
    try:
        df = pd.read_csv(LOG_PATH, encoding="utf-8-sig")
        if df.empty: 
            return 0
        df["data"] = pd.to_datetime(df["data_hora"]).dt.date
        hoje = date.today()
        return df[(df["data"] == hoje) & (df["status"] == "ENVIADO")].shape[0]
    except Exception:
        return 0

def ja_enviado_mesma_planilha(arquivo, telefone):
    """Evita reenviar para o mesmo número se a MESMA planilha for usada novamente."""
    ensure_log()
    try:
        df = pd.read_csv(LOG_PATH, encoding="utf-8-sig")
        if df.empty:
            return False
        mask = (df["arquivo"] == arquivo) & (df["telefone"].astype(str) == str(telefone)) & (df["status"] == "ENVIADO")
        return df[mask].shape[0] > 0
    except Exception:
        return False

# --------------------------------------------------
# Checagem de sessão (verifica se está conectado ao WhatsApp)
# --------------------------------------------------
def checar_sessao():
    try:
        r = requests.get(API_URL_CHECK, headers=HEADERS, timeout=5)
        if r.status_code == 200:
            js = r.json()
            # Estado pode vir como "open", "connecting", "connected"
            state = js.get("instance", {}).get("state") or js.get("state", "")
            return str(state).lower() == "connected"
        return False
    except Exception as e:
        print("Erro ao checar sessão:", e)
        return False

# --------------------------------------------------
# Helpers de dados/mensagem
# --------------------------------------------------
def formatar_numero(telefone_raw):
    if pd.isna(telefone_raw) or str(telefone_raw).strip() == "":
        return None
    telefone = re.sub(r'\D', '', str(telefone_raw).strip())
    if len(telefone) < 8:
        return None
    if len(telefone) in [8, 9]:
        telefone = f"21{telefone}"
    if not telefone.startswith("55"):
        telefone = f"55{telefone}"
    return telefone

def gerar_mensagem(sindico, condominio, modelo):
    primeiro_nome = str(sindico).split()[0].capitalize() if sindico else "Síndico"
    cond = condominio if condominio else "seu condomínio"
    return (modelo
            .replace("{NOME DO CLIENTE}", primeiro_nome)
            .replace("{NOME DO CONDOMÍNIO}", cond))

# --------------------------------------------------
# Rotas Flask
# --------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html',
                           mensagem_padrao=MENSAGEM_PADRAO,
                           enviados_hoje=enviados_hoje())

@app.route('/preview', methods=['POST'])
def preview():
    file = request.files['arquivo']
    mensagem_custom = request.form.get('mensagem', '').strip()
    modelo_mensagem = mensagem_custom if mensagem_custom else MENSAGEM_PADRAO

    if not file or file.filename == '':
        return "Nenhum arquivo enviado."

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    file.save(filepath)

    df = pd.read_excel(filepath) if file.filename.endswith('.xlsx') else pd.read_csv(filepath)
    if not all(col in df.columns for col in ['Telefones', 'Síndico', 'Condomínio']):
        return "A planilha deve conter as colunas 'Telefones', 'Síndico' e 'Condomínio'."

    contatos = []
    for _, row in df.iterrows():
        sindico = str(row['Síndico']).strip() if not pd.isna(row['Síndico']) else ""
        condominio = str(row['Condomínio']).strip() if not pd.isna(row['Condomínio']) else ""
        telefones_raw = str(row['Telefones']).strip()
        if not telefones_raw or telefones_raw.lower() == "nan":
            continue
        for t in telefones_raw.split("/"):
            numero = formatar_numero(t)
            if numero:
                contatos.append({
                    "sindico": sindico,
                    "condominio": condominio,
                    "formatado": numero,
                    "mensagem": gerar_mensagem(sindico, condominio, modelo_mensagem)
                })

    return render_template('preview.html',
                           contatos=contatos,
                           arquivo=file.filename,
                           mensagem=modelo_mensagem)

# --------------------------------------------------
# Envio em segundo plano (thread)
# --------------------------------------------------
def _thread_envio(selecionados, mappings, arquivo, modelo_mensagem):
    PROGRESS.update({
        "running": True,
        "total": len(selecionados),
        "enviados": 0,
        "falhas": 0,
        "pulados": 0,
        "atual": "",
        "mensagem": "Iniciando envios...",
        "erro": ""
    })
    CANCELAR["flag"] = False

    if not checar_sessao():
        PROGRESS.update({"running": False, "erro": "WhatsApp não conectado. Escaneie o QR Code e tente novamente."})
        return

    enviados_do_dia = enviados_hoje()
    LIMITE_DIA = 50

    for idx, numero in enumerate(selecionados, start=1):
        if CANCELAR["flag"]:
            PROGRESS.update({"mensagem": "Envio cancelado pelo usuário.", "running": False})
            return

        PROGRESS["atual"] = numero
        PROGRESS["mensagem"] = f"Enviando {idx} de {len(selecionados)}..."

        if enviados_do_dia >= LIMITE_DIA:
            PROGRESS["pulados"] += 1
            append_log(arquivo, mappings[numero]["sindico"], mappings[numero]["condominio"], numero, "PULADO_LIMITE", "")
            continue

        if ja_enviado_mesma_planilha(arquivo, numero):
            PROGRESS["pulados"] += 1
            append_log(arquivo, mappings[numero]["sindico"], mappings[numero]["condominio"], numero, "PULADO_DUPLICADO", "")
            continue

        msg_final = gerar_mensagem(mappings[numero]["sindico"], mappings[numero]["condominio"], modelo_mensagem)

        payload = {"number": f"{numero}", "text": msg_final}

        try:
            r = requests.post(API_URL_SEND, headers=HEADERS, json=payload, timeout=15)
            if r.status_code == 200:
                PROGRESS["enviados"] += 1
                enviados_do_dia += 1
                append_log(arquivo, mappings[numero]["sindico"], mappings[numero]["condominio"], numero, "ENVIADO", msg_final)
            else:
                PROGRESS["falhas"] += 1
                append_log(arquivo, mappings[numero]["sindico"], mappings[numero]["condominio"], numero, f"FALHA_{r.status_code}", msg_final)
        except Exception as e:
            PROGRESS["falhas"] += 1
            append_log(arquivo, mappings[numero]["sindico"], mappings[numero]["condominio"], numero, "FALHA_EXCECAO", str(e))

        time.sleep(10)

    PROGRESS.update({"running": False, "mensagem": "Envio concluído."})

@app.route('/enviar', methods=['POST'])
def enviar():
    arquivo = request.form['arquivo']
    mensagem_custom = request.form.get('mensagem', '').strip()
    modelo_mensagem = mensagem_custom if mensagem_custom else MENSAGEM_PADRAO

    selecionados = request.form.getlist('selecionados')
    if not selecionados:
        return "Nenhum contato selecionado para envio."

    maps_raw = request.form.getlist('map')
    mappings = {}
    for item in maps_raw:
        try:
            numero, sindico, condominio = item.split("||", 2)
            mappings[numero] = {"sindico": sindico, "condominio": condominio}
        except:
            pass

    if PROGRESS.get("running", False):
        return "Já existe um envio em andamento. Aguarde finalizar."
    th = threading.Thread(target=_thread_envio, args=(selecionados, mappings, arquivo, modelo_mensagem), daemon=True)
    th.start()

    return render_template('progresso.html')

@app.route('/status')
def status():
    return jsonify(PROGRESS)

@app.route('/cancelar', methods=['POST'])
def cancelar():
    if PROGRESS.get("running", False):
        CANCELAR["flag"] = True
        return "cancelado"
    return "nenhum_envio"

# ---------- Histórico & Download ----------
@app.route('/historico')
def historico():
    ensure_log()
    try:
        df = pd.read_csv(LOG_PATH, encoding="utf-8-sig")
    except Exception:
        df = pd.DataFrame(columns=["data_hora","arquivo","sindico","condominio","telefone","status","mensagem"])
    registros = df.to_dict(orient="records")
    return render_template('historico.html', registros=registros)

@app.route('/download_log')
def download_log():
    ensure_log()
    return send_file(LOG_PATH, as_attachment=True, download_name='log_envios.csv')

if __name__ == '__main__':
    app.run(debug=True, port=5050)
