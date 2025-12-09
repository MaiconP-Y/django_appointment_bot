import os
import json
from groq import Groq
from core_ia.services_agents.prompts_agents import prompt_consul_cancel

# O 'ConsultaService' agora é desacoplado (versão corrigida no item 1)
from core_ia.services_agents.consulta_services_ia import ConsultaService

from core_ia.services_agents.tool_reset import finalizar_user, REROUTE_COMPLETED_STATUS, RESET_SIGNAL 
from services.redis_client import delete_history, delete_session_state
from services.metrics import registrar_evento
import logging

logger = logging.getLogger(__name__)
# 2. Definição Unificada das Tools

def cancelar_consulta(chat_id: str, numero_consulta: int) -> dict:
    # Apenas garante que a ToolFunction chame a versão limpa do serviço
    return ConsultaService.cancelar_agendamento(chat_id, numero_consulta)
TOOLS_CANCEL = [
    {
        "type": "function",
        "function": {
            "name": "finalizar_user",
            "description": "Função utilizada para resetar sessão/voltar ao menu. Deve ser chamada se o usuário mudar de assunto ou pedir para sair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_str": { "type": "string", "description": "Histórico para re-roteamento." },
                },
                "required": ["history_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "cancelar_consulta",
            "description": "Cancela uma consulta existente baseada no número identificador (ID UX).",
            "parameters": {
                "type": "object",
                "properties": {
                    "numero_consulta": {
                        "type": "integer",
                        "description": "O número da consulta (ex: 1, 2) que aparece na lista [1]."
                    }
                },
                "required": ["numero_consulta"]
            }
        }
    }
]

class Agent_cancel:
    def __init__(self):
        try:
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        except Exception as e:
            raise EnvironmentError("GROQ_API_KEY não configurada.") from e
    
    def generate_cancel(self, history_str: str, chat_id: str) -> str:
        
        # --- Prepara Dados (Lógica de Negócio) ---
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
        
        # --- Monta Prompt ---
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
                tools=TOOLS_CANCEL, # Schema atualizado
                tool_choice="auto"
            )
            
            response_message = chat_completion.choices[0].message
            
            # --- Processamento de Tools ---
            if response_message.tool_calls:
                mensagens.append(response_message) # Adiciona contexto para a próxima volta (se houver)

                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    args = json.loads(tool_call.function.arguments)
                    
                    if function_name == "finalizar_user":
                        args['history_str'] = history_str    
                        
                        # 1. 🟢 Executa a Tool Pura (obtém o sinal e a mensagem)
                        result_output = finalizar_user(history_str)
                        
                        # 2. 🎯 VERIFICA O SINAL DE RESET
                        if result_output.startswith(RESET_SIGNAL):
                            # 3. ✅ AÇÃO DE INFRAESTRUTURA (Redis I/O) - EXECUTADA NO AGENT
                            delete_session_state(chat_id) 
                            delete_history(chat_id)
                            
                            # 4. Extrai a mensagem para reroute (o que o usuário disse por último)
                            _, message_to_reroute = result_output.split('|', 1)
                            
                            from core_ia.ia_core import agent_service # <-- Importa o serviço para acesso ao router
                            service_agent_instance = agent_service()
                            clean_context_for_router = f"User: {message_to_reroute}"

                            # Chama o router principal
                            response = service_agent_instance.router(
                                clean_context_for_router, 
                                chat_id, 
                                reroute_signal="__FORCE_ROUTE_INTENT__" # Sinal para forçar o roteamento
                            )

                            # Retorna a resposta final do router para o Worker
                            return response 
                        
                        # 6. Fallback (Se a Tool for usada para retornar algo que não seja reset)
                        tool_content = result_output

                    elif function_name == "cancelar_consulta":
                        numero = args.get("numero_consulta")
                        # CÓDIGO NOVO (No Agent, Exemplo: agent_consul_cancel.py):
                        tool_result_dict = ConsultaService.cancelar_agendamento(chat_id, numero) 

                        if tool_result_dict.get("status") == "SUCCESS":
                            delete_session_state(chat_id) 
                            delete_history(chat_id)       
                            # 🎯 NOVO: PEGA O GOOGLE EVENT ID RETORNADO PELO SERVIÇO
                            gcal_event_id = tool_result_dict.get("google_event_id", "ID_NAO_ENCONTRADO")
                            
                            # 🎯 CHAMA A MÉTRICA DE SUCESSO AQUI (SRP)
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=gcal_event_id,
                                tipo_metrica='cancelamento',
                                status='success',
                                detalhes=f"Cancelamento do slot {numero} efetuado."
                            )
                            # Retorna a mensagem de sucesso (Gerente decide o que falar)
                            final_message = "Sua consulta foi cancelada com sucesso! Qualquer duvida é só chamar!"
                            return f"{REROUTE_COMPLETED_STATUS}|{final_message}"

                        else:
                            error_message = tool_result_dict.get("message", "Ocorreu um erro desconhecido.")
                            
                            event_id_for_metric = tool_result_dict.get("google_event_id", f"slot_{numero}_falha_ux")
                            
                            # 🎯 MÉTRICA DE FALHA (Negócio ou Técnica)
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=event_id_for_metric,
                                tipo_metrica='cancelamento',
                                status=tool_result_dict.get("status", "error").lower(),
                                detalhes=f"Falha ao cancelar slot {numero}. Motivo: {error_message}"
                            )
                            
                            # Permite que o LLM responda à falha.
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
            # 🎯 MÉTRICA DE FALHA CRÍTICA
            registrar_evento(
                cliente_id=chat_id,
                event_id='agent_critical_fail',
                tipo_metrica='cancelamento',
                status='error_critico',
                detalhes=f"Falha CRÍTICA no agente cancel (Groq/JSON/Infra): {str(e)}"
            )
            logger.error(f"Erro CRÍTICO no Agent_cancel (Groq/Tool-Call): {e}", exc_info=True) # 🎯 BOA PRÁTICA: Log com Traceback
            return "Desculpe, tive um problema técnico. Tente digitar 'menu' para reiniciar."