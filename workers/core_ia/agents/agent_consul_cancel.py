import os
import json
from groq import Groq
from core_ia.services_agents.prompts_agents import prompt_consul_cancel
from core_ia.services_agents.consulta_services_ia import ConsultaService

from core_ia.services_agents.tool_reset import finalizar_user, REROUTE_COMPLETED_STATUS, RESET_SIGNAL 
from services.redis_client import delete_history, delete_session_state
from services.metrics import registrar_evento
import logging
from core_ia.services_agents.tools_schemas import TOOLS_CANCEL

logger = logging.getLogger(__name__)

class Agent_cancel:
    def __init__(self):
        try:
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        except Exception as e:
            raise EnvironmentError("GROQ_API_KEY não configurada.") from e
    
    def generate_cancel(self, history_str: str, chat_id: str) -> str:
        lista_consultas = ConsultaService.listar_agendamentos(chat_id)
        
        if lista_consultas:
            formatted_list = []
            for item in lista_consultas:
                formatted_list.append(
                    f"[{item['appointment_number']}] - Data: {item['data']} às {item['hora']}"
                )
            consultas_str = "\n".join(formatted_list)
        else:
            consultas_str = "Nenhuma consulta agendada."
        system_prompt = f"""
        {prompt_consul_cancel}
        
        --- DADOS EM TEMPO REAL ---
        Aqui estão as consultas atuais deste usuário:
        {consultas_str} 
        ---------------------------
        """
        
        mensagens = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": history_str}
        ]
        
        try:
            # --- Chamada LLM ---
            chat_completion = self.client.chat.completions.create(
                messages=mensagens,
                model="llama-3.3-70b-versatile",
                temperature=0.1,
                tools=TOOLS_CANCEL,
                tool_choice="auto"
            )
            
            response_message = chat_completion.choices[0].message
            if response_message.tool_calls:
                mensagens.append(response_message)

                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    
                    if function_name == "finalizar_user":
                        args['history_str'] = history_str    
                        result_output = finalizar_user(history_str)
                        if result_output.startswith(RESET_SIGNAL):
                            delete_session_state(chat_id) 
                            delete_history(chat_id)
                            _, message_to_reroute = result_output.split('|', 1)
                            from core_ia.ia_core import agent_service
                            service_agent_instance = agent_service()
                            clean_context_for_router = f"User: {message_to_reroute}"
                            response = service_agent_instance.router(
                                clean_context_for_router, 
                                chat_id, 
                                reroute_signal="__FORCE_ROUTE_INTENT__"
                            )
                            return response 
                        
                        tool_content = result_output

                    elif function_name == "cancelar_consulta":
                        numero = args.get("numero_consulta")
                        tool_result_dict = ConsultaService.cancelar_agendamento(chat_id, numero) 
                        if tool_result_dict.get("status") == "SUCCESS":
                            delete_session_state(chat_id) 
                            delete_history(chat_id)       
                            gcal_event_id = tool_result_dict.get("google_event_id", "ID_NAO_ENCONTRADO")
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=gcal_event_id,
                                tipo_metrica='cancelamento',
                                status='success',
                                detalhes=f"Cancelamento do slot {numero} efetuado."
                            )
                            final_message = "Sua consulta foi cancelada com sucesso! Qualquer duvida é só chamar!"
                            return f"{REROUTE_COMPLETED_STATUS}|{final_message}"

                        else:
                            error_message = tool_result_dict.get("message", "Ocorreu um erro desconhecido.")
                            event_id_for_metric = tool_result_dict.get("google_event_id", f"slot_{numero}_falha_ux")
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=event_id_for_metric,
                                tipo_metrica='cancelamento',
                                status=tool_result_dict.get("status", "error").lower(),
                                detalhes=f"Falha ao cancelar slot {numero}. Motivo: {error_message}"
                            )
                            tool_content = error_message

                    mensagens.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(tool_content)
                    })
                    
                final_response = self.client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=mensagens
                )
                return final_response.choices[0].message.content

            return response_message.content
            
        except Exception as e:
            registrar_evento(
                cliente_id=chat_id,
                event_id='agent_critical_fail',
                tipo_metrica='cancelamento',
                status='error_critico',
                detalhes=f"Falha CRÍTICA no agente cancel (Groq/JSON/Infra): {str(e)}"
            )
            logger.error(f"Erro CRÍTICO no Agent_cancel (Groq/Tool-Call): {e}", exc_info=True) 
            raise