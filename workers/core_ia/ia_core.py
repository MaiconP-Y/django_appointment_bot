from services.redis_client import update_session_state, get_user_profile_cache, set_user_profile_cache

from core_ia.agents.agent_register import Agent_register
from core_ia.agents.agent_date import Agent_date
from core_ia.agents.agent_router import Agent_router
from core_ia.agents.agent_consul_cancel import Agent_cancel
from core_ia.agents.agent_info import Agent_info
import logging 
from core_api.django_api_service import DjangoApiService
logger = logging.getLogger(__name__)

# 🎯 NOVO SINAL GLOBAL
REROUTE_SIGNAL = "__FORCE_ROUTE_INTENT__" 
MENSAGEM_ERRO_SUPORTE = "Desculpe, ocorreu um erro técnico inesperado no nosso sistema de IA. Por favor, entre em contato diretamente com nosso suporte."


def get_user_name_from_db(chat_id: str) -> str | None:
    """
    Busca o nome do usuário no BaaS, utilizando o Redis Cache como primeira linha.
    """
    
    # 1. TENTAR LER DO CACHE REDIS (Busca o valor que agora tem TTL de 24h)
    cached_user_data = get_user_profile_cache(chat_id)
    
    if cached_user_data:
        logger.info(f"✅ User data para {chat_id} ENCONTRADO no Redis Cache (TTL de 24h).")
        return cached_user_data.get('username')

    # 2. SE NÃO ESTIVER NO CACHE, BUSCAR NO DJANGO
    logger.info(f"⏳ User data para {chat_id} não encontrado no cache. Buscando via HTTP...")
    
    try:
        user_data = DjangoApiService.get_user_data(chat_id)
        
        if user_data and user_data.get('status') == 'SUCCESS':
            # 3. SALVAR NO CACHE (Aplica o TTL de 24h)
            set_user_profile_cache(chat_id, user_data)
            logger.info(f"💾 User data de {chat_id} salvo no cache com TTL de 3h.")
            
            return user_data.get('username')
        
        return None 

    except Exception as e:
        logger.error(f"❌ Erro CRÍTICO HTTP ao buscar dados de user para {chat_id}: {e}", exc_info=True)
        return None

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
            if reroute_signal == REROUTE_SIGNAL: # Para o tool_reset indicar sem estado, força o estado para nova detecção de intenção
                step_decode = None
            
            # Lógica HUMANE_SERVICE mantida no escopo original:
            if step_decode == 'HUMANE_SERVICE':
                
                return "Ok, solicitação detectada com sucesso. Um de nossos agentes entrará em contato com você em breve. A partir de agora, nosso bot LLM não processará mais suas mensagens."
            
            response = ""
            
            if user_name:
                
                # A lógica abaixo usa a variável 'step_decode' (que agora é None se houver re-route)
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
                        # 🎯 NOVO ESTADO INICIAL: Começa na busca
                        update_session_state(chat_id, registration_step='AGENT_DATE_SEARCH')
                        # Passa o estado inicial para o agente
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
            # 🎯 TRATAMENTO DE ERRO CRÍTICO (FALHA NA GROQ OU NOS AGENTES)
            logger.error(f"Erro CRÍTICO no serviço de IA para chat_id {chat_id}: {e}", exc_info=True)
            from services.waha_api import Waha
            try:
                # 1. Envia a mensagem amigável
                waha_service = Waha() 
                waha_service.send_support_contact(chat_id)
                
            except Exception as waha_e:
                logger.error(f"Falha ao enviar mensagem de suporte via WAHA: {waha_e}")
            
            # 3. Retorna um sinal REROUTE_COMPLETED vazio/curto para notificar o Worker
            # que a resposta já foi enviada e o processamento deve ser finalizado.
            from core_ia.services_agents.tool_reset import REROUTE_COMPLETED_STATUS
            return f"{REROUTE_COMPLETED_STATUS}|{MENSAGEM_ERRO_SUPORTE}"