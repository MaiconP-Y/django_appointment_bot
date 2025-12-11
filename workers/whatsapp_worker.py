"""
Worker independente para processar fila do WhatsApp - VERSÃO COM FLUXO DO WEBHOOK FUNCIONAL
"""

import json
import logging


from services.redis_client import (
    add_message_to_history, 
    get_recent_history,
    get_redis_client,
    check_and_set_message_id,
    get_session_state, 
    delete_history
)
from services.waha_api import Waha
from workers.core_ia.ia_core import agent_service
from core_ia.services_agents.tool_reset import REROUTE_COMPLETED_STATUS

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("whatsapp-worker")
QUEUE_NAME = "new_user_queue"

class WhatsAppWorker:
    def __init__(self): 
        self.redis_client = None
        self.setup_connections()
        self.redis_client = get_redis_client()
        self.service_agent = agent_service()
        self.service_waha = Waha()
        
    def setup_connections(self):
        try:
            self.redis_client = get_redis_client()
            self.redis_client.ping()
        except Exception as e:
            logger.error(f"❌ Erro na configuração do Worker: {e}")
            raise

    def process_incoming_message_data(self, raw_json_payload):
        """
        Lógica: Decodificar -> Duplicata Check -> Processar -> Re-enfileirar (se falhar).
        """
        try:
            main_data = json.loads(raw_json_payload.decode('utf-8'))
        except Exception as e:
            logger.error(f"❌ Erro ao decodificar JSON: {e}")
            return 

        try:
            message_data = main_data.get("payload", {})
            chat_id = message_data.get("from")
            message_text = message_data.get("body", "").strip()
            message_id = message_data.get("id")
            message_type = message_data.get("_data", {}).get("type")
            
            if not message_id:
                logger.warning("Payload sem message_id válido. Descartando (Ex: Notificação de leitura).")
                return 
            
            if not check_and_set_message_id(message_id):
                logger.warning(f"⚠️ Duplicata ID: {message_id} descartada pelo Worker (SETNX falhou).")
                return 
            
            if message_type != 'chat':
                friendly_message = "Olá! Por favor, *envie sua mensagem como texto digitado* para que eu possa processá-la. Não consigo processar áudios, imagens, vídeos ou outros formatos no momento. Obrigado pela compreensão!"
                self.service_waha.send_whatsapp_message(chat_id, friendly_message)
                logger.info(f"Tipo de mensagem '{message_type}' detectado e rejeitado para {chat_id}. Worker finalizado.")
                return
            
            session_data = get_session_state(chat_id)
            step_bytes = session_data.get(b'registration_step') 
            active_step_decode = step_bytes.decode('utf-8') if step_bytes else None
            if active_step_decode == 'HUMANE_SERVICE':
                return
            add_message_to_history(chat_id, "User", message_text)

            history = get_recent_history(chat_id, limit=10)
            history_str = "\n".join(history)
            logger.info(f"Contexto final para o LLM:\n{history_str}")
            
            self.service_waha.start_typing(chat_id)
            try:
                response = self.service_agent.router(history_str, chat_id, step_decode=active_step_decode) 
            finally:
                self.service_waha.stop_typing(chat_id)

            ACTIVATION_MESSAGE = "Ok, solicitação detectada com sucesso. Um de nossos agentes entrará em contato com você em breve. A partir de agora, nosso bot LLM não processará mais suas mensagens."

            if response.strip().startswith(REROUTE_COMPLETED_STATUS):
                _, final_bot_response = response.split('|', 1) 
                self.service_waha.send_whatsapp_message(chat_id, final_bot_response)   

                logger.info(f"Processamento de RE-ROTEAMENTO BEM-SUCEDIDO para {chat_id}. Worker finalizado.")
                return
            
            if response == ACTIVATION_MESSAGE:
                self.service_waha.send_whatsapp_message(chat_id, response) 
                delete_history(chat_id) 
                logger.info(f"Handover para {chat_id} COMPLETO. Histórico DELETADO e ciclo de Worker finalizado.")
                return

            self.service_waha.send_whatsapp_message(chat_id, response)
            add_message_to_history(chat_id, "Bot", response)
            logger.info(f"Processamento para {chat_id} BEM-SUCEDIDO. Histórico Bot SALVO.")
            
        except Exception as e:
            logger.error(f"❌ Falha CRÍTICA no processamento para {chat_id}: {e}", exc_info=True)
            MENSAGEM_ERRO_FATAL = "Nosso sistema de comunicação e fila de mensagens está com falhas. Por favor, entre em contato diretamente com nosso suporte."

            try:
                self.service_waha.send_whatsapp_message(chat_id, MENSAGEM_ERRO_FATAL)
                self.service_waha.send_support_contact(chat_id)
            except Exception as waha_e:
                logger.error(f"Falha ao enviar mensagem de suporte via WAHA: {waha_e}")

            self.redis_client.rpush(QUEUE_NAME, raw_json_payload)
            logger.warning(f"♻️ Mensagem {message_id} re-enfileirada para reprocessamento.")
            raise 

    def listen_queue(self):
        queue_name = QUEUE_NAME
        logger.info(f"Worker INICIADO. Aguardando mensagens na fila persistente '{queue_name}' (BLPOP)...")

        while True:
            try:
                result = self.redis_client.blpop(queue_name, timeout=30) 
                if result:
                    raw_json_payload = result[1] 
                    logger.info(f"📨 Payload LIDO da fila persistente.")
                    self.process_incoming_message_data(raw_json_payload)

            except Exception as e:
                logger.error(f"❌ Erro no loop de escuta (worker): {e}")
                import time; time.sleep(5)
                
    def run(self):
        logger.info("🚀 WhatsApp Worker INICIADO - Versão Corrigida")
        try:
            self.listen_queue()
        except KeyboardInterrupt:
            logger.info("⏹️ Worker interrompido pelo usuário")
        except Exception as e:
            logger.error(f"💥 Erro fatal no worker: {e}")
            raise

if __name__ == "__main__":
    worker = WhatsAppWorker()
    worker.run()