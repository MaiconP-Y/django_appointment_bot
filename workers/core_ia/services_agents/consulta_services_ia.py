import logging
from datetime import datetime
from typing import Dict, Any, List, Optional

# REMOVIDAS: from django.db import transaction, from chatbot_api.models import UserRegister

# IMPORTS DO SERVIÇO DE AGENDA EXTERNA (Google Calendar)
from services.service_api_calendar import ServicesCalendar
# IMPORTS DO NOVO SERVIÇO DE COMUNICAÇÃO HTTP (BaaS)
from core_api.django_api_service import DjangoApiService
from services.redis_client import delete_user_profile_cache


logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# CLASSE DE SERVIÇO DE NEGÓCIO (LÓGICA PURA, SEM ACESSO DIRETO AO DB)
# ------------------------------------------------------------------

class ConsultaService:
    
    @staticmethod
    def criar_agendamento_db(chat_id: str, google_event_id: str, start_time_iso: str) -> dict:
        """
        [TOOL FUNCTION]
        Delega a criação do agendamento para o BaaS via HTTP.
        A lógica de slots, locks e transação está no Views.py do Django.
        """
        payload = {
            "chat_id": chat_id,
            "google_event_id": google_event_id,
            "start_time_iso": start_time_iso
        }
        
        # Chama o serviço HTTP. Zero ORM.
        response = DjangoApiService.save_appointment(payload)
        if response.get('status') == 'SUCCESS':
            delete_user_profile_cache(chat_id)
            logger.info(f"✅ Agendamento salvo via BaaS - Cliente: {chat_id}")
        else:
            logger.error(f"❌ Falha ao salvar agendamento via BaaS: {response.get('message')}")
            
        return response

    @staticmethod
    def listar_agendamentos(chat_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        [HELPER FUNCTION]
        Busca dados de usuário e agendamentos ativos no BaaS.
        """
        user_data = DjangoApiService.get_user_data(chat_id)
        
        if not user_data or user_data.get("status") != "SUCCESS":
            return []
            
        # O BaaS (views.py) já faz o filtro de datas passadas e retorna a lista limpa.
        return user_data.get("appointments", [])

    @staticmethod
    def cancelar_agendamento(chat_id: str, numero_consulta: int) -> dict:
        """
        [TOOL FUNCTION]
        Cancela a consulta no Google Calendar e atualiza o slot no DB via HTTP.
        """
        
        # 1. Busca os dados de GCal ID do BaaS
        user_data = DjangoApiService.get_user_data(chat_id)
        appointments = user_data.get('appointments', [])
        
        try:
            consulta_a_cancelar = next(
                (c for c in appointments if c.get('appointment_number') == numero_consulta),
                None
            )
        except StopIteration:
            return {"status": "FAILURE", "message": "Consulta não encontrada ou número inválido."}

        if not consulta_a_cancelar:
            return {"status": "FAILURE", "message": "Número de consulta inválido ou já expirada."}

        event_id_to_cancel = consulta_a_cancelar['gcal_id']
        appointment_datetime_iso = consulta_a_cancelar['datetime_iso']
        
        # 2. Chama o Google Calendar (lógica externa mantida)
        try:
            # ✅ ADIÇÃO 1: Inicializa o serviço se não estiver pronto (Fiel à lógica antiga)
            if not ServicesCalendar.service:
                ServicesCalendar.inicializar_servico()

            # ✅ CORREÇÃO FINAL: Passar o objeto 'service' como primeiro argumento (Resolve o TypeError)
            resp_google = ServicesCalendar.deletar_evento(
                ServicesCalendar.service, # <--- ARGUMENTO OBRIGATÓRIO FALTANDO
                event_id_to_cancel
            )
            
            # Ajuste de status para fidelidade
            if resp_google.get('status') != 'SUCCESS':
                return {"status": "ERROR", "message": f"Erro ao cancelar no Google Calendar: {resp_google['message']}"}
        except Exception as e:
            logger.error(f"❌ Falha de comunicação com Google Calendar: {e}", exc_info=True)
            return {"status": "ERROR", "message": "Falha de comunicação com o Google Calendar."}
        
        # 3. Chama o BaaS via HTTP (para limpar o slot atomicamente no DB)
        payload_db = {
            "chat_id": chat_id,
            "numero_consulta": numero_consulta,
        }
        
        response_data = DjangoApiService.cancel_appointment(payload_db)
        
        # 4. Checagem final de integridade
        if response_data.get('status') == 'SUCCESS':
            logger.info(f"✅ Cancelamento COMPLETO - Cliente: {chat_id}, Slot: {numero_consulta}")
            delete_user_profile_cache(chat_id)
            return {
                "status": "SUCCESS", 
                "message": "Consulta cancelada e removida da agenda.",
                "google_event_id": event_id_to_cancel, 
                "canceled_datetime": appointment_datetime_iso
            }
        else:
            # ERRO CRÍTICO: GCal cancelou, mas o DB falhou na limpeza.
            error_message = response_data.get('message', 'Falha desconhecida na limpeza do DB (BaaS).')
            logger.error(f"⚠️ Alerta: GCal cancelado, mas falha ao limpar DB! ({chat_id}): {error_message}")
            return {"status": "ERROR_DB_CLEANUP", "message": f"Consulta cancelada no Google Calendar, mas houve erro ao atualizar o sistema: {error_message}"}