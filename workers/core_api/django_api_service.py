# Arquivo: chatbot_api/services/core_api/django_api_service.py

import requests
import logging
import os
from typing import Optional, Dict

logger = logging.getLogger(__name__)

# Configuração de ambiente (Ajuste conforme a rede Docker)
DJANGO_BAAS_URL = os.environ.get('DJANGO_BAAS_URL', 'http://django-web:8000/api/v1/')
API_TOKEN = os.environ.get('DJANGO_BAAS_API_TOKEN') 

AUTH_HEADERS = {
    'Authorization': f'Token {API_TOKEN}',
    'Content-Type': 'application/json'
}

class DjangoApiService:
    """
    Proxy HTTP para o Django Backend as a Service (BaaS).
    """
    
    @staticmethod
    def get_user_data(chat_id: str) -> Optional[Dict]:
        """Busca dados de registro de usuário (e agendamentos) via API JSON."""
        url = f"{DJANGO_BAAS_URL}user/{chat_id}/"
        try:
            response = requests.get(url, headers=AUTH_HEADERS, timeout=5)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro HTTP ao buscar user {chat_id}: {e}")
            return None 
            
    @staticmethod
    def save_appointment(payload: Dict) -> Dict:
        """Salva um novo agendamento, DELEGANDO a lógica de slots/transação ao BaaS."""
        url = f"{DJANGO_BAAS_URL}agendamentos/salvar/"
        try:
            response = requests.post(url, json=payload, headers=AUTH_HEADERS, timeout=5)
            if response.status_code == 409:
                # O BaaS (Django) retorna 409 + a mensagem de limite no JSON.
                # Lemos o JSON diretamente e retornamos para o worker processar a FALHA.
                try:
                    return response.json()
                except requests.exceptions.JSONDecodeError:
                    return {"status": "FAILURE", "message": "Conflito de agendamento (formato de resposta inválido do BaaS)."}
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro HTTP ao salvar agendamento: {e}")
            return {"status": "ERROR", "message": "Falha de comunicação com o BaaS. Tente novamente."}
            
    @staticmethod
    def cancel_appointment(payload: Dict) -> Dict:
        """Limpa o slot de agendamento no DB via API JSON (Delegate)."""
        url = f"{DJANGO_BAAS_URL}agendamentos/cancelar/"
        try:
            response = requests.post(url, json=payload, headers=AUTH_HEADERS, timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro HTTP ao cancelar agendamento: {e}")
            return {"status": "ERROR", "message": "Falha de comunicação com o BaaS. Tente novamente."}
            
    @staticmethod
    def register_user(payload: Dict) -> Dict:
        """Registra um novo usuário no DB via API JSON (Delegate)."""
        url = f"{DJANGO_BAAS_URL}user/register/"
        try:
            response = requests.post(url, json=payload, headers=AUTH_HEADERS, timeout=5)
            if response.status_code == 409: 
                return {"status": "FAILURE", "message": "Usuário já existe."}
            
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Erro HTTP ao registrar usuário: {e}")
            return {"status": "ERROR", "message": "Falha de comunicação com o BaaS."}
    
    @staticmethod
    def log_metric(payload: Dict) -> Dict:
        """Envia um log de métrica para o BaaS via API JSON (Delegate)."""
        url = f"{DJANGO_BAAS_URL}metrics/log/"
        try:
            # O timeout pode ser mais curto para métricas (não bloqueia a resposta ao usuário)
            response = requests.post(url, json=payload, headers=AUTH_HEADERS, timeout=3) 
            response.raise_for_status() 
            return response.json()
        except requests.exceptions.RequestException as e:
            # Não é um erro CRÍTICO, apenas um aviso, pois o evento do usuário já foi processado.
            logger.warning(f"⚠️ Falha ao registrar métrica via HTTP: {e}") 
            # Retorna falha, mas o agente geralmente ignora em casos de métrica
            return {"status": "HTTP_FAILURE", "message": f"Falha de comunicação: {e}"}
            