from services.redis_client import update_session_state

from core_ia.agents.agent_register import Agent_register
from core_ia.agents.agent_date import Agent_date
from core_ia.agents.agent_router import Agent_router
from core_ia.agents.agent_consul_cancel import Agent_cancel
from core_ia.agents.agent_info import Agent_info
from core_ia.utils.user_data_service import get_user_name_from_db
import logging 

logger = logging.getLogger(__name__)

REROUTE_SIGNAL = "__FORCE_ROUTE_INTENT__" 
MENSAGEM_ERRO_SUPORTE = "Desculpe, ocorreu um erro técnico inesperado no nosso sistema de IA. Por favor, entre em contato diretamente com nosso suporte."

class agent_service(): 
    """
    Serviço de IA minimalista. Atua como proxy entre o Worker e o Roteador de Agentes.
    Gerencia o estado e formata o histórico para a API Groq.
    """
    def __init__(self):
        self.registration_agent = Agent_register()
        self.date_agent = Agent_date(router_agent_instance=self) 
        self.router_agent = Agent_router()
        self.agent_consul_cancel = Agent_cancel()
        self.agent_info = Agent_info()
        
    def router(self, history_str: str, chat_id: str, step_decode: str = None, reroute_signal: str = None) -> str:
        """
        Delega o trabalho de roteamento.
        """
        try:
            user_name = get_user_name_from_db(chat_id)
            if reroute_signal == REROUTE_SIGNAL:
                step_decode = None
            
            if step_decode == 'HUMANE_SERVICE':
                
                return "Ok, solicitação detectada com sucesso. Um de nossos agentes entrará em contato com você em breve. A partir de agora, nosso bot LLM não processará mais suas mensagens."
            
            response = ""
            
            if user_name:
                if step_decode: 
                    if step_decode in ['AGENT_DATE_SEARCH', 'AGENT_DATE_CONFIRM']:
                        response = self.date_agent.generate_date(step_decode, history_str, chat_id, user_name)
                    
                    elif step_decode == 'AGENT_CAN_VERIF':
                        response = self.agent_consul_cancel.generate_cancel(history_str, chat_id)
                    return response
                        
                else: 
                    response = self.router_agent.route_intent(history_str)
                    if response == 'ativar_agent_atendimento_humano':
                        update_session_state(chat_id, registration_step='HUMANE_SERVICE')
                        return "Ok, solicitação detectada com sucesso. Um de nossos agentes entrará em contato com você em breve. A partir de agora, nosso bot LLM não processará mais suas mensagens."
                    if response == 'ativar_agent_marc':
                        update_session_state(chat_id, registration_step='AGENT_DATE_SEARCH')
                        response = self.date_agent.generate_date('AGENT_DATE_SEARCH', history_str, chat_id, user_name)
                        
                    elif response == 'ativar_agent_ver_cancel':
                        update_session_state(chat_id, registration_step='AGENT_CAN_VERIF')
                        response = self.agent_consul_cancel.generate_cancel(history_str, chat_id)
                    elif response == 'ativar_agent_info':
                        response = self.agent_info.generate_info(history_str, user_name)
                    return response
            else:      
                response = self.registration_agent.generate_register(history_str, chat_id)

            return response
            
        except Exception as e:
            logger.error(f"Erro CRÍTICO no serviço de IA para chat_id {chat_id}: {e}", exc_info=True)
            from services.waha_api import Waha
            try:
                waha_service = Waha() 
                waha_service.send_support_contact(chat_id)
                
            except Exception as waha_e:
                logger.error(f"Falha ao enviar mensagem de suporte via WAHA: {waha_e}")

            from core_ia.services_agents.tool_reset import REROUTE_COMPLETED_STATUS
            return f"{REROUTE_COMPLETED_STATUS}|{MENSAGEM_ERRO_SUPORTE}"