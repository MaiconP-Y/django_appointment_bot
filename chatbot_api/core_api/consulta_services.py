# Arquivo: chatbot_api/services/services_agents/consulta_services.py (REFATORADO)

import logging
# Apenas datetime é mantido, pois é uma biblioteca padrão do Python
from datetime import datetime
# >>> REMOVIDOS (Para desacoplar do Django):
# from django.db import transaction
# from django.utils import timezone
# from chatbot_api.models import UserRegister
from django.db import transaction
from chatbot_api.models import UserRegister
from datetime import datetime
from django.utils import timezone
from services.service_api_calendar import ServicesCalendar
# Importa o novo Proxy HTTP
from services.django_api_service import DjangoApiService 
# ----------------------------------------------------------------------

logger = logging.getLogger(__name__)

class ConsultaService:
    
    @staticmethod
    def criar_agendamento_db(chat_id: str, google_event_id: str, start_time_iso: str) -> dict:
        """
        Salva o agendamento no UserRegister. Retorna um dicionário de status.
        (NÃO FAZ I/O DE REDIS)
        """
        try:
            with transaction.atomic():
                user = UserRegister.objects.select_for_update().get(chat_id=chat_id)
                new_datetime = datetime.fromisoformat(start_time_iso)
                agora = timezone.now()
                
                is_slot1_free = not user.appointment1_gcal_id or (user.appointment1_datetime and user.appointment1_datetime < agora)
                
                if is_slot1_free:
                    user.appointment1_datetime = new_datetime
                    user.appointment1_gcal_id = google_event_id
                    user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
                    
                    # ... (código de métricas inalterado) ...
                    
                    logger.info(f"✅ Agendamento salvo no slot 1 - Cliente: {chat_id}")
                    
                    # 🎯 RETORNO REFATORADO: Retorna o status e os dados de negócio
                    return {"status": "SUCCESS", "slot": 1, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}

                is_slot2_free = not user.appointment2_gcal_id or (user.appointment2_datetime and user.appointment2_datetime < agora)
                
                if is_slot2_free:
                    user.appointment2_datetime = new_datetime
                    user.appointment2_gcal_id = google_event_id
                    user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id'])

                    # ... (código de métricas inalterado) ...
                    
                    logger.info(f"✅ Agendamento salvo no slot 2 - Cliente: {chat_id}")
                    
                    # 🎯 RETORNO REFATORADO: Retorna o status e os dados de negócio
                    return {"status": "SUCCESS", "slot": 2, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
                else:
                    # 🎯 RETORNO REFATORADO: Retorna a falha
                    return {"status": "FAILURE", "message": "Limite de agendamentos atingido. Você pode ter no máximo 2 consultas ativas."}
                    
        except UserRegister.DoesNotExist:
            # ... (código de métricas inalterado) ...
            logger.error(f"❌ Usuário {chat_id} não registrado ao tentar salvar agendamento")
            # 🎯 RETORNO REFATORADO: Retorna a falha
            return {"status": "FAILURE", "message": "Usuário não registrado."}
            
        except ValueError as e:
            # ... (código de métricas inalterado) ...
            logger.error(f"❌ Erro de validação ao salvar agendamento: {e}")
            # 🎯 RETORNO REFATORADO: Retorna a falha
            return {"status": "FAILURE", "message": str(e)}
            
        except Exception as e:
            # ... (código de métricas inalterado) ...
            logger.error(f"❌ Erro ao salvar agendamento no DB: {e}")
            # 🎯 RETORNO REFATORADO: Retorna a falha
            return {"status": "ERROR", "message": "Ocorreu um erro interno ao tentar agendar."}

    @staticmethod
    def cancelar_consulta(chat_id: str, numero_consulta: int) -> dict:
        """
        Cancela uma consulta no Google Calendar e atualiza o DB via HTTP (com BaaS).
        """
        try:
            # 1. BUSCA O USUÁRIO (VIA HTTP) - Substitui UserRegister.objects.get()
            user_data = DjangoApiService.get_user_data(chat_id)
            if not user_data:
                return {"status": "FAILURE", "message": "Usuário não encontrado."}
                
            # 2. Lógica para extrair o gcal_id (A LÓGICA DE SLOT PERMANECE AQUI, SÓ QUE EM DICTS)
            if numero_consulta == 1:
                event_id_to_cancel = user_data.get('appointment1_gcal_id')
                appointment_datetime_iso = user_data.get('appointment1_datetime') 
            elif numero_consulta == 2:
                event_id_to_cancel = user_data.get('appointment2_gcal_id')
                appointment_datetime_iso = user_data.get('appointment2_datetime')
            else:
                return {"status": "FAILURE", "message": "Número de consulta inválido (1 ou 2)."}
                
            if not event_id_to_cancel:
                return {"status": "FAILURE", "message": f"Não encontrei consulta ativa no slot {numero_consulta}."}
                
            # 3. CHAMA O GOOGLE CALENDAR
            if not ServicesCalendar.service:
                ServicesCalendar.inicializar_servico()
                
            resp_google = ServicesCalendar.deletar_evento(
                ServicesCalendar.service, 
                event_id_to_cancel
            )
            
            if resp_google['status'] == 'ERROR':
                if 'Evento já não existia' not in resp_google.get('message', ''):
                     return {"status": "ERROR", "message": f"Erro ao cancelar no Google Calendar: {resp_google.get('message', 'Erro desconhecido')}"}
            
            # 4. CHAMA O BAAS (VIA HTTP) para LIMPAR o slot no banco de dados
            payload_db = {
                "chat_id": chat_id,
                "numero_consulta": numero_consulta,
            }
            
            response_data = DjangoApiService.cancel_appointment(payload_db)
            
            if response_data.get('status') == 'SUCCESS':
                logger.info(f"✅ Cancelamento registrado - Cliente: {chat_id}, Slot: {numero_consulta}")
                return {
                    "status": "SUCCESS", 
                    "message": response_data.get('message', 'Consulta cancelada e removida da agenda.'),
                    "google_event_id": event_id_to_cancel, 
                    "canceled_datetime": appointment_datetime_iso if appointment_datetime_iso else "" 
                }
            
            return response_data
            
        except Exception as e:
            logger.error(f"❌ Erro no fluxo de cancelamento para slot {numero_consulta} (HTTP/GCal): {e}", exc_info=True)
            return {"status": "ERROR", "message": "Ocorreu um erro interno ao tentar cancelar."}