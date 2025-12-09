import os
import json 
from groq import Groq
# 🎯 Importe o novo sinal de reset e a classe de serviço
from core_ia.services_agents.tool_reset import finalizar_user, REROUTE_COMPLETED_STATUS, RESET_SIGNAL

from core_ia.services_agents.prompts_agents import prompt_date_search, prompt_date_confirm
from services.service_api_calendar import ServicesCalendar, validar_data_nao_passada, validar_dia_nao_domingo

# O 'ConsultaService' agora é desacoplado (versão corrigida no item 1)
from core_ia.services_agents.consulta_services_ia import ConsultaService 

from services.redis_client import delete_history, delete_session_state, update_session_state
from datetime import datetime
import logging
from services.metrics import registrar_evento # Ajustado conforme o caminho no projeto

logger = logging.getLogger(__name__)

groq_service = Groq()
services_calendar = ServicesCalendar()
AGENT_DATE_SEARCH = "AGENT_DATE_SEARCH"
AGENT_DATE_CONFIRM = "AGENT_DATE_CONFIRM"
REGISTRATION_TOOL_SCHEMA_SEARCH =[
    {
        "type": "function", 
        "function": {
            "name": "finalizar_user",
            "description": "Função utilizada para resetar seção.  Deve ser chamada se o usuário pedir para cancelar o agendamento ou começar do zero.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_str": { 
                        "type": "string",
                        "description": "O histórico completo da conversa até o momento, para re-roteamento."
                    },
                },
                "required": ["history_str"] 
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "ver_horarios_disponiveis",
            "description": "Verifica os horários disponíveis de 60 minutos para o dia em específico.  Retorna uma lista de strings HH:MM ou uma mensagem de erro.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "A data fornecida pelo usuário, formatada obrigatoriamente como YYYY-MM-DD.  Ex: 2025-11-20"
                    }
                },
                "required": ["data"] 
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "exibir_proximos_horarios_flex",
            "description": "Busca e exibe os próximos 11 horários disponíveis no calendário a partir de hoje. Use esta função quando o usuário perguntar 'quais horários disponíveis' ou não especificar uma data.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]
REGISTRATION_TOOL_SCHEMA_CONFIRM = [
    {
        "type": "function", 
        "function": {
            "name": "finalizar_user",
            "description": "Função utilizada para resetar seção.  Deve ser chamada se o usuário pedir para cancelar o agendamento ou começar do zero.",
            "parameters": {
                "type": "object",
                "properties": {
                    "history_str": { 
                        "type": "string",
                        "description": "O histórico completo da conversa até o momento, para re-roteamento."
                    },
                },
                "required": ["history_str"] 
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "agendar_consulta_1h",
            "description": "Cria um novo evento de 1 hora na agenda.  Esta função DEVE ser chamada APENAS depois que a disponibilidade for verificada e o usuário escolher um horário.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time_str": {
                        "type": "string",
                        "description": "Data e hora de início da consulta, formatada como ISO 8601 completo, incluindo fuso horário. Ex: 2025-11-20T14:00:00-03:00"
                    },
                    "summary": {
                        "type": "string",
                        "description": "Breve título do evento, como 'Agendamento de Consulta de [Nome do Usuário]'"
                    }
                },
                "required": ["start_time_str"] 
            }
        }
    }
]

class Agent_date():
    """
    Classe de serviço dedicada a interagir com a API da Groq, usando o histórico completo (history_str)
    para manter o contexto e delegar ações de registro via Tool Calling.
    """
    def __init__(self, router_agent_instance):
        try:
            self.client = Groq(api_key=os.environ. get("GROQ_API_KEY"))
            ServicesCalendar.inicializar_servico()
            self.calendar_services = ServicesCalendar()
            self.router_agent = router_agent_instance
        except Exception as e:
            raise EnvironmentError("A variável GROQ_API_KEY não está configurada. ") from e
    def exibir_proximos_horarios_flex(self, chat_id: str) -> str:
        """
        Tool: Busca 11 slots disponíveis usando a estratégia escalonada (4->10->30 dias).
        Formata e retorna a lista legível para o usuário.
        """
        # Obtenção do serviço de forma canônica
        service = ServicesCalendar.service
        
        # 📞 Chamada da função eficiente que criamos (limite = 11)
        resultado_tool = ServicesCalendar.buscar_proximos_disponiveis(
            service=service, 
            limite_slots=11, 
            duracao_minutos=60  # Padrão de 60 minutos
        )

        if resultado_tool.get("status") == "SUCCESS":
            slots_encontrados = resultado_tool.get("available_slots", [])
            
            if not slots_encontrados:
                # ✅ Retorno Direto de Aviso (Go Way: Short-Circuiting)
                return (
                    f"❌ Nossos horários estão lotados nas próximas quatro semanas. "
                    f"Tente novamente em alguns dias."
                )
            else:
                # NOVO CÓDIGO AQUI: AGRUPAMENTO POR DATA
                
                slots_agrupados = {}
                
                # O formato do slot['legivel'] é 'DD/MM - HH:MM' (conforme service_api_calendar.py)
                for slot in slots_encontrados:
                    # Divide em data ('DD/MM') e hora ('HH:MM')
                    parts = slot['legivel'].split(' - ')
                    if len(parts) == 2:
                        data_parte = parts[0] # Ex: '03/12'
                        hora_parte = parts[1] # Ex: '07:00'
                        
                        # Adiciona a hora à lista daquela data específica
                        if data_parte not in slots_agrupados:
                            slots_agrupados[data_parte] = []
                        
                        slots_agrupados[data_parte].append(hora_parte)

                # NOVO CÓDIGO AQUI: FORMATAÇÃO DA STRING FINAL AGRUPADA
                
                slots_str_agrupado = []
                for data, horas in slots_agrupados.items():
                    # Junta as horas separadas por vírgula
                    horas_str = ", ".join(horas)
                    slots_str_agrupado.append(f"""Data {data}:
 {horas_str}""")

                slots_final_output = "\n".join(slots_str_agrupado)

                return (f"""Encontrei {len(slots_encontrados)} horários disponíveis próximos:
{slots_final_output}

Qual destes horários você gostaria de agendar? (Ex: 'Quero dia 04/12 às 10:00')"""
)
        else:
            # ✅ Retorno de Erro Técnico
            error_message = resultado_tool.get('message', 'Erro desconhecido ao buscar horários.')
            
            # 🎯 MÉTRICA DE FALHA: AGORA COM O chat_id CORRETO
            registrar_evento(
                cliente_id=chat_id, # 🎯 USANDO O chat_id PASSADO
                event_id='busca_flex_gcal',
                tipo_metrica='agendamento',
                status='error',
                detalhes=f"Falha na busca de horários flexíveis. Motivo: {error_message}"
            )
            return f"❌ Falha ao buscar horários disponíveis: {error_message}"
    def salvar_agendamento(chat_id: str, google_event_id: str, start_time_iso: str) -> dict:
        # Apenas garante que a ToolFunction chame a versão limpa do serviço
        return ConsultaService.criar_agendamento_db(chat_id, google_event_id, start_time_iso)
    def generate_date(self, step_decode: str, history_str: str, chat_id: str, user_name: str) -> str:
        """
        Gera uma resposta da IA, usando a string do histórico completo como a última mensagem do usuário.
        Atua como roteador interno baseado no step_decode (estado atual).
        """
        if step_decode == AGENT_DATE_SEARCH:
            prompt_content = prompt_date_search
            tool_schema = REGISTRATION_TOOL_SCHEMA_SEARCH

        elif step_decode == AGENT_DATE_CONFIRM:
            prompt_content = prompt_date_confirm
            tool_schema = REGISTRATION_TOOL_SCHEMA_CONFIRM

        mensagens = [
            {
                "role": "system",
                "content": f"O NOME COMPLETO do usuário é: {user_name}. {prompt_content}",
            },
            {
                "role": "user",
                "content": history_str
            }
        ]
        
        try:
            chat_completion = self.client.chat.completions.create(
                messages=mensagens,
                model="llama-3.3-70b-versatile",
                tools=tool_schema,
                tool_choice="auto",
                temperature=0.0, 
            )

            response_message = chat_completion.choices[0].message
            resposta_ia = response_message.content
            
            if response_message.tool_calls:
                available_functions = {
                    "agendar_consulta_1h": ServicesCalendar.criar_evento,
                    "ver_horarios_disponiveis": ServicesCalendar.buscar_horarios_disponiveis,
                    "finalizar_user": finalizar_user, 
                    "exibir_proximos_horarios_flex": self.exibir_proximos_horarios_flex, 
                }
                
                mensagens. append(response_message)
                
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function. name
                    function_to_call = available_functions[function_name]
                    
                    function_args = json.loads(tool_call.function. arguments)
                    if function_name == "finalizar_user":
                        function_args['history_str'] = history_str    
                        
                        # 1. Executa a Tool Pura (obtém o sinal e a mensagem)
                        result_output = finalizar_user(history_str)
                        
                        # 2. 🎯 VERIFICA O SINAL DE RESET
                        if result_output.startswith(RESET_SIGNAL):
                            # 3. ✅ AÇÃO DE INFRAESTRUTURA (Redis I/O) - EXECUTADA NO AGENT
                            delete_session_state(chat_id) 
                            delete_history(chat_id)
                            
                            # 4. Extrai a mensagem para reroute
                            _, message_to_reroute = result_output.split('|', 1)
                            
                            # 5. CHAMA O ROUTER (Orquestração final)
                            from core_ia.ia_core import agent_service # NOVO: Importar agent_service para roteamento
                            service_agent_instance = agent_service()
                            clean_context_for_router = f"User: {message_to_reroute}"

                            response = service_agent_instance.router(
                                clean_context_for_router, 
                                chat_id, 
                                reroute_signal="__FORCE_ROUTE_INTENT__" # Sinal para forçar o roteamento
                            )
                            # Retorna a resposta final do router para o Worker
                            return response 
                        
                        # Fallback se a Tool for usada para retornar algo que não seja reset
                        tool_content = result_output
                        
                    elif function_name == "agendar_consulta_1h":
                        start_time_str = function_args.get("start_time_str")
                        try:
                            # Tenta extrair a data para validação
                            start_dt = datetime.fromisoformat(start_time_str)
                            # 🎯 CORREÇÃO 1: Formata a data para o padrão de VALIDAÇÃO (DD/MM/YYYY)
                            data_para_validacao = start_dt.strftime("%d/%m/%Y")
                        except ValueError:
                            # Se o formato ISO for inválido
                            tool_content = f"❌ Erro de formato de data: {start_time_str}"
                            continue # Volta ao loop de tool_calls para o LLM responder

                        # 1. VALIDAÇÃO DE DATA PASSADA
                        validacao_passada = validar_data_nao_passada(data_para_validacao)
                        if not validacao_passada.get("status") == "SUCCESS":
                            # ✅ CORREÇÃO 2: Usar .get('message')
                            return "A data que você informou já passou. Por favor, escolha uma data futura."
                        
                        # 2. VALIDAÇÃO DE DOMINGO
                        validacao_domingo = validar_dia_nao_domingo(data_para_validacao)
                        if not validacao_domingo.get("status") == "SUCCESS":
                            # ✅ CORREÇÃO 3: Usar .get('message')
                            return "Não agendamos consultas aos domingos. Por favor, escolha outro dia."
                        
                        function_args['chat_id'] = chat_id
                        function_args['name'] = user_name

                        resultado_tool = function_to_call(ServicesCalendar.service, **function_args)
                        
                        if isinstance(resultado_tool, dict) and resultado_tool.get("status") == "SUCCESS":
                            gcal_event_id = resultado_tool.get("event_id")
                            start_time_iso = resultado_tool.get("start_time")
                            
                            
                            baas_result = ConsultaService.criar_agendamento_db(
                                chat_id=chat_id,
                                google_event_id=gcal_event_id,
                                start_time_iso=start_time_iso 
                            )
                            if baas_result.get("status") != "SUCCESS":
                                error_message = baas_result.get('message', 'Erro desconhecido ao salvar no BaaS.')
                                
                                # Se falhou no BaaS, precisamos cancelar o evento no GCal!
                                ServicesCalendar.deletar_evento(
                                    ServicesCalendar.service, 
                                    gcal_event_id
                                )
                                
                                # 🎯 MÉTRICA DE FALHA: FALHA NO BAAS
                                registrar_evento(
                                    cliente_id=chat_id,
                                    event_id=gcal_event_id,
                                    tipo_metrica='agendamento',
                                    status='failed',
                                    detalhes=f"Falha: BaaS negou o agendamento. Evento GCal {gcal_event_id} cancelado. Motivo: {error_message}"
                                )
                                delete_session_state(chat_id)
                                delete_history(chat_id)

                                # ✅ Retorna a mensagem correta do BaaS para o usuário.
                                return f"{REROUTE_COMPLETED_STATUS}|{error_message}"

                            dt_obj = datetime.fromisoformat(start_time_iso)
                            data_formatada = dt_obj.strftime("%d/%m/%Y")
                            hora_formatada = dt_obj. strftime("%H:%M")
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=gcal_event_id,
                                tipo_metrica='agendamento',
                                status='success',
                                detalhes=f"Consulta agendada para {data_formatada}, as {hora_formatada}"
                            )
                            delete_session_state(chat_id)
                            delete_history(chat_id)
                    
                            return (f"""{REROUTE_COMPLETED_STATUS}|Agendamento Confirmado, {user_name}
Sua consulta foi marcada com sucesso para o dia *{data_formatada}* às {hora_formatada}. 
Fique tranquilo(a), enviaremos um lembrete próximo ao dia do evento."""
                                )
                            
                            

                        else:
                            # Se a Tool falhar (ex: indisponibilidade de último segundo)
                            gcal_error_message = resultado_tool.get('message', 'Erro desconhecido ao tentar criar evento no Google Calendar.')
                            
                            # 🎯 MÉTRICA DE FALHA: FALHA NA CRIAÇÃO DO EVENTO GCAL
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=f"tentativa_{start_time_str}",
                                tipo_metrica='agendamento',
                                status='failed',
                                detalhes=f"Falha ao criar evento GCal. Motivo: {gcal_error_message}"
                            )
                            tool_content = f"Erro no agendamento: {gcal_error_message}"

                    elif function_name == "ver_horarios_disponiveis":
                        data_YYYY_MM_DD = function_args.get("data") # YYYY-MM-DD
                        
                        try:
                            dt_obj = datetime.strptime(data_YYYY_MM_DD, "%Y-%m-%d")
                            # Reformata YYYY-MM-DD para o formato esperado: DD/MM/YYYY
                            data_para_validacao = dt_obj.strftime("%d/%m/%Y")
                        except ValueError:
                            # Se a tool passar um formato ruim, a validação de formato falhará
                            data_para_validacao = data_YYYY_MM_DD 
                            
                        # 1. VALIDAÇÃO DE DATA PASSADA
                        validacao_passada = validar_data_nao_passada(data_para_validacao)
                        if not validacao_passada.get('status') == 'SUCCESS': 
                            return "A data que você informou já passou. Por favor, escolha uma data futura."
                        
                        # 2. VALIDAÇÃO DE DOMINGO
                        validacao_domingo = validar_dia_nao_domingo(data_para_validacao)
                        if not validacao_domingo.get('status') == 'SUCCESS':
                            return "Não agendamos consultas aos domingos. Por favor, escolha outro dia."
                        
                        resultado_tool = ServicesCalendar.buscar_horarios_disponiveis(ServicesCalendar.service, **function_args)

                        if not (isinstance(resultado_tool, dict) and resultado_tool. get("status") == "SUCCESS"):
                            error_message = resultado_tool.get('message', 'Erro desconhecido ao verificar horários.')
                            
                            # 🎯 MÉTRICA DE FALHA: FALHA NA BUSCA DE DISPONIBILIDADE
                            registrar_evento(
                                cliente_id=chat_id,
                                event_id=f"busca_{data_YYYY_MM_DD}",
                                tipo_metrica='agendamento',
                                status='error',
                                detalhes=f"Falha na busca de disponibilidade GCal. Motivo: {error_message}"
                            )
                            
                            return f"{REROUTE_COMPLETED_STATUS}|Falha ao verificar horários: {error_message}\n\nInforme uma nova data (AAAA-MM-DD)."

                        available_slots = resultado_tool.get("available_slots", [])

                        # Usa a data já validada e formatada para exibição
                        data_formatada = data_para_validacao
                        
                        
                        if not available_slots:
                                # Retorno sem mudança de estado (continua SEARCH, pedindo nova data)
                                return (f"""{REROUTE_COMPLETED_STATUS}|Nenhum horário disponível em **{data_formatada}**.\n\nInforme outra data para verificar (AAAA-MM-DD).""")
                        else:
                            # 🎯 TRANSIÇÃO DE ESTADO! O sucesso da busca muda o fluxo.
                            update_session_state(chat_id, registration_step=AGENT_DATE_CONFIRM) 
                            delete_history(chat_id)
                            slots_str = "\n".join([f"  - {slot}" for slot in available_slots])
                            
                            # Retorno com a nova instrução para o usuário
                            return (f"""Os Horários disponíveis em *{data_formatada}*:
{slots_str}
Qual horário deseja agendar? (Informe o horário no formato HH:MM)""")         

                    elif function_name == "exibir_proximos_horarios_flex":
                        
                        # 1. Execução: Chama o método que retorna a STRING final (slots ou erro com "❌").
                        resultado_str = function_to_call(chat_id)
                        
                        # 2. DECISÃO E I/O BASEADO NA STRING
                        if resultado_str.startswith("❌"): # Se for erro (sem slots ou erro técnico)
                            # Retorna o erro final, mantendo o estado AGENT_DATE_SEARCH para nova tentativa.
                            return f"{REROUTE_COMPLETED_STATUS}|{resultado_str}"
                            
                        # Se for sucesso (não começou com "❌"), transiciona o estado para AGENT_DATE_CONFIRM.
                        delete_history(chat_id)    
                        update_session_state(chat_id, registration_step=AGENT_DATE_CONFIRM)
                        
                        # 3. Retorna a string formatada de horários para o Worker/Usuário.
                        return resultado_str

                    mensagens.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool", 
                            "name": function_name,
                            "content": f"Resultado da Ferramenta {function_name}: {tool_content}"
                        }
                    )
                    
                final_completion = self.client.chat.completions. create(
                    model="llama-3.3-70b-versatile",
                    messages=mensagens 
                )
            
                return final_completion.choices[0].message.content
            
            return resposta_ia
            
        except Exception as e:
            # 🎯 MÉTRICA DE FALHA: ERRO CRÍTICO NO AGENTE
            registrar_evento(
                cliente_id=chat_id,
                event_id='agent_critical_fail',
                tipo_metrica='agendamento',
                status='error_critico',
                detalhes=f"Falha CRÍTICA no agente date (Groq/JSON/Infra): {str(e)}"
            )
            logger.error(f"Erro CRÍTICO no Agent_date (Groq/Tool-Call): {e}", exc_info=True)
            return "Desculpe, estou tendo problemas técnicos para responder agora."