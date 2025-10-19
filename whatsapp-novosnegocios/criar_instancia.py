import requests

API_URL = "http://localhost:8081"
API_KEY = "ABCD"
headers = {"apikey": API_KEY}

payload = {
    "instanceName": "novosnegocios",
    "token": "token123"
}

# Tenta primeiro com /instances/create
url_1 = f"{API_URL}/instances/create"
url_2 = f"{API_URL}/instance/create"

try:
    response = requests.post(url_1, headers=headers, json=payload)
    data = response.json()
    if response.status_code == 200:
        print("✅ Instância criada com sucesso (endpoint plural):")
        print(data)
    else:
        print("🔁 Tentando endpoint alternativo...")
        response = requests.post(url_2, headers=headers, json=payload)
        data = response.json()
        print("Resposta final:")
        print(data)
except Exception as e:
    print("❌ Erro de conexão:", e)
