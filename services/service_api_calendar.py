import os
import datetime
from datetime import datetime, timedelta, timezone
import logging
logger = logging.getLogger(__name__)

try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
except ImportError:
    logging.warning("Bibliotecas Google API não encontradas. Usando mocks para compilação.")
    class service_account:
        @staticmethod
        def Credentials(): pass
    def build(): pass

BR_TIMEZONE = timezone(timedelta(hours=-3))

GOOGLE_CALENDAR_ID = os.environ.get('GOOGLE_CALENDAR_ID', 'maiconwantuil@gmail.com')
CALENDAR_SCOPE = ['https://www.googleapis.com/auth/calendar'] 
GOOGLE_CREDENTIALS_PATH = os.environ.get('GOOGLE_CREDENTIALS_PATH', 'caminho/para/o/seu-arquivo-de-credenciais.json')
calendar_id = GOOGLE_CALENDAR_ID 

class ToolException(Exception):
    """Exceção customizada para erros de ferramenta."""
    pass

def validar_dia_nao_domingo(data_str: str) -> dict:
    """
    Valida se a data fornecida é um Domingo. Retorna status dict.
    (NÃO FAZ I/O DE REDIS)
    """
    try:
        data_consulta = datetime.strptime(data_str, "%d/%m/%Y")
        if data_consulta.weekday() == 6:  # 6 é Domingo
            return {"status": "FAILURE", "message": "Não fazemos agendamentos aos domingos. Por favor, escolha outro dia."}
        
        return {"status": "SUCCESS", "message": "Data válida."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato de data inválido. Use DD/MM/AAAA."}

def validar_data_nao_passada(data_str: str) -> dict:
    """
    Valida se a data fornecida está no passado. Retorna status dict.
    (NÃO FAZ I/O DE REDIS)
    """
    try:
        data_consulta = datetime.strptime(data_str, "%d/%m/%Y").date()
        hoje = datetime.now(timezone(timedelta(hours=-3))).date() 
        
        if data_consulta < hoje:
            return {"status": "FAILURE", "message": "Dia é domingo."}
            
        return {"status": "SUCCESS", "message": "Dia é válido."}
    except ValueError:
        return {"status": "ERROR", "message": "Formato de data inválido. Use DD/MM/AAAA."}


def gerar_horarios_disponiveis() -> list:
    """
    Gera uma lista de slots de 60 minutos (HH:MM) dentro do horário de trabalho (7:00h às 20:00h).
    """
    horarios = []
    start_time = datetime.strptime("07:00", "%H:%M")
    end_time = datetime.strptime("20:00", "%H:%M")
    
    current_time = start_time
    while current_time < end_time:
        horarios.append(current_time.strftime("%H:%M"))
        current_time += timedelta(minutes=60)
        
    return horarios

def is_slot_busy(slot_time_str: str, busy_blocks: list, data: str, duration_minutos: int) -> bool:
    """Verifica se o slot de agendamento (HH:MM) se sobrepõe a qualquer bloco ocupado."""
    slot_start_dt = datetime.strptime(f"{data}T{slot_time_str}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BR_TIMEZONE)
    
    slot_end_dt = slot_start_dt + timedelta(minutes=duration_minutos)
    
    for block in busy_blocks:
        try:
            busy_start_dt = datetime.fromisoformat(block['start'])
            busy_end_dt = datetime.fromisoformat(block['end'])
        except ValueError:
            continue 

        if slot_start_dt < busy_end_dt and slot_end_dt > busy_start_dt:
            return True
            
    return False

def buscar_disponibilidade_escalonada(
    service, 
    limite_slots: int = 3, 
    duracao_minutos: int = 60,
    margens_dias: list[int] = None
) -> dict:
    """
    Busca os próximos slots livres usando a estratégia escalonada (4->10->30 dias),
    ignorando explicitamente qualquer dia que seja Domingo.
    
    (Lógica externa de iteração - SRP)
    """
    if not service:
        return {"status": "ERROR", "message": "Erro: Objeto de serviço do Google Calendar não inicializado."}
    if margens_dias is None:
        margens_dias = [4, 10, 30] 
        
    hoje = datetime.now(BR_TIMEZONE).date()
    slots_sugeridos = []
    for margem in margens_dias:
        logging.info(f"Iniciando busca flexível: Margem de +{margem} dias (sem domingos).")
        for i in range(margem):
            data_atual = hoje + timedelta(days=i)
            if data_atual.weekday() == 6: 
                logging.debug(f"⏭️ Pulando {data_atual.strftime('%Y-%m-%d')} - É Domingo.")
                continue       
            data_str = data_atual.strftime("%Y-%m-%d")
            resultado = ServicesCalendar.buscar_horarios_disponiveis(
                service=service, 
                data=data_str, 
                duracao_minutos=duracao_minutos
            )
            
            if resultado['status'] == 'SUCCESS':
                for hora in resultado['available_slots']:
                    data_hora_iso = f"{data_str}T{hora}:00-03:00"
                    data_hr_obj = datetime.strptime(f"{data_str} {hora}", "%Y-%m-%d %H:%M")
                    data_hr_legivel = data_hr_obj.strftime("%d/%m - %H:%M")
                    slots_sugeridos.append({
                        'iso_time': data_hora_iso,
                        'legivel': data_hr_legivel
                    })
                    if len(slots_sugeridos) >= limite_slots:
                        logging.info(f"Limite de {limite_slots} slots atingido na margem de {margem} dias.")
                        return {
                            "status": "SUCCESS", 
                            "available_slots": slots_sugeridos
                        }

    if slots_sugeridos:
        return {"status": "SUCCESS", "available_slots": slots_sugeridos}
    else:
        return {
            "status": "SUCCESS", 
            "available_slots": [],
            "message": "Nenhum horário disponível foi encontrado nas próximas quatro semanas úteis."
        }


# ═══════════════════════════════════════════════════════════════════════════════
# CLASSE DE SERVIÇO (COESA - APENAS LOGICA DE API)
# ═══════════════════════════════════════════════════════════════════════════════

class ServicesCalendar:
    
    service = None 
    
    @staticmethod
    def inicializar_servico():
        """
        Inicializa o objeto de serviço do Google Calendar com credenciais de serviço.
        """
        if ServicesCalendar.service:
            logging.info("Serviço do Google Calendar já inicializado.")
            return True
            
        logging.info(f"Tentando inicializar serviço com arquivo em: {GOOGLE_CREDENTIALS_PATH}")
        
        try:
            credentials = service_account.Credentials.from_service_account_file(
                GOOGLE_CREDENTIALS_PATH, 
                scopes=CALENDAR_SCOPE
            )
            
            ServicesCalendar.service = build('calendar', 'v3', credentials=credentials)
            logging.info("Serviço do Google Calendar inicializado com sucesso.")
            return True
            
        except Exception as e:
            logging.error(f"ERRO DE INICIALIZAÇÃO E AUTENTICAÇÃO: {e}")
            logging.error("Verifique se o GOOGLE_CREDENTIALS_PATH e o arquivo JSON estão corretos.")
            return False

    @staticmethod
    def buscar_eventos_do_dia(service, data: str) -> list:
        """Busca todos os eventos ocupados no dia especificado (events().list())."""
        try:
            time_min = f'{data}T07:00:00-03:00'
            time_max = f'{data}T20:00:00-03:00'

            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            return events_result.get('items', [])
            
        except Exception as e:
            return []

    @staticmethod
    def buscar_horarios_disponiveis(service, data: str, duracao_minutos: int = 60):
        """
        Calcula os horários disponíveis (livres) usando o endpoint freebusy do Google. 
        """
        try:
            try:
                data_date_obj = datetime.strptime(data, "%Y-%m-%d").date()
            except ValueError:
                return {"status": "ERROR", "message": f"Formato inválido para a data: '{data}'. Use 'YYYY-MM-DD'. "}

            time_min = f'{data}T07:00:00-03:00'
            time_max = f'{data}T20:00:00-03:00'
            query_body = {
                "timeMin": time_min,
                "timeMax": time_max,
                "items": [{"id": calendar_id}]
            }
            freebusy_response = service.freebusy().query(body=query_body).execute()
            busy_blocks = freebusy_response.get('calendars', {}).get(calendar_id, {}).get('busy', [])
            horarios = gerar_horarios_disponiveis() 
            livres = []
            hoje = datetime.now(BR_TIMEZONE).date()
            now_with_margin = datetime.now(BR_TIMEZONE) + timedelta(minutes=30)
            past_margin_passed = False 
            
            for h in horarios:
                is_busy = is_slot_busy(h, busy_blocks, data, duracao_minutos)
                
                if not is_busy:
                    if data_date_obj == hoje:
                        if past_margin_passed:
                            livres.append(h)
                            continue 

                        slot_dt = datetime.strptime(f"{data}T{h}:00", "%Y-%m-%dT%H:%M:%S").replace(tzinfo=BR_TIMEZONE)
                        if slot_dt >= now_with_margin:
                            livres.append(h)
                            past_margin_passed = True 
                    
                    else:
                        livres.append(h)

            if not livres:
                return {"status": "SUCCESS", "available_slots": [], "message": f"Não há horários disponíveis para {data}. "}

            return {"status": "SUCCESS", "available_slots": livres}
            
        except ToolException as e:
            return {"status": "ERROR", "message": f"Erro na validação da ferramenta: {e}"}
        except Exception as e:
            logging.error(f"Erro inesperado no cálculo de disponibilidade (freebusy): {e}")
            return {"status": "ERROR", "message": f"Erro inesperado ao buscar horários disponíveis: {e}"}


    @staticmethod
    def criar_evento(
        service, 
        start_time_str: str, 
        chat_id: str,
        name: str,
        summary: str = None, 
        time_zone: str = 'America/Sao_Paulo'
    ):
        """
        Cria um novo evento de 1 hora de duração (60 minutos) na agenda principal.
        
        Inclui a verificação de disponibilidade de último segundo (chamando buscar_horarios_disponiveis).
        """
        if not service:
            return {"status": "ERROR", "message": "Erro: Objeto de serviço do Google Calendar não inicializado."}

        try:
            start_dt = datetime.fromisoformat(start_time_str)
        except ValueError:
            return {"status": "ERROR", "message": f"Formato inválido para start_time_str: '{start_time_str}'. Use o formato ISO 8601 completo."}

        data_str = start_dt.strftime("%Y-%m-%d")
        hora_str = start_dt.strftime("%H:%M")
        disponiveis = ServicesCalendar.buscar_horarios_disponiveis(
            service=service, 
            data=data_str, 
            duracao_minutos=60 
        )
        
        if disponiveis['status'] == 'ERROR' or hora_str not in disponiveis.get('available_slots', []):
            logging.warning(f"❌ Tentativa de agendamento em slot indisponível: {start_time_str}")
            return {
                "status": "ERROR", 
                "message": f"❌ O horário {hora_str} do dia {start_dt.strftime('%d/%m/%Y')} não está mais disponível."
            }
        
        logging.info(f"✅ Slot {start_time_str} confirmado como disponível.")
        DURACAO_MINUTOS = 60
        end_dt = start_dt + timedelta(minutes=DURACAO_MINUTOS)
        end_time_str = end_dt.isoformat()
        final_summary = f"CONSUL Nome:{name} - Cliente ID:{chat_id}"

        event_body = {
            'summary': final_summary, 
            'start': {
                'dateTime': start_time_str, 
                'timeZone': time_zone,
            },
            'end': {
                'dateTime': end_time_str,   
                'timeZone': time_zone,
            },
            'reminders': {
                'useDefault': False,
                'overrides': [
                    {'method': 'email', 'minutes': 24 * 60},
                    {'method': 'popup', 'minutes': 10},
                ],
            },
        }

        try:
            event = service.events().insert(
                calendarId=calendar_id, 
                body=event_body,
            ).execute()
            
            return {
                "status": "SUCCESS", 
                "event_link": event.get('htmlLink'), 
                "event_id": event.get('id'),
                "start_time": start_time_str
            }
            
        except Exception as e:
            logging.error(f"Erro ao criar evento na agenda: {e}")
            return {"status": "ERROR", "message": f"Falha ao criar o evento na agenda: {e}"}
        
    @staticmethod
    def deletar_evento(service, event_id: str):
        """
        Deleta um evento do Google Calendar pelo ID.
        """
        if not service:
            return {"status": "ERROR", "message": "Serviço de calendário não inicializado."}
            
        try:
            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()
            
            logging.info(f"Evento {event_id} deletado do Google Calendar com sucesso.")
            return {"status": "SUCCESS", "message": "Evento cancelado no Google Calendar."}
            
        except Exception as e:
            logging.error(f"Erro ao deletar evento {event_id}: {e}")
            if "404" in str(e) or "410" in str(e):
                return {"status": "SUCCESS", "message": "Evento já não existia no Google Calendar."}
                
            return {"status": "ERROR", "message": f"Erro ao deletar evento: {e}"}
        
    @staticmethod
    def buscar_proximos_disponiveis(service, limite_slots: int = 3, duracao_minutos: int = 60) -> dict:
        """
        Calcula os próximos slots livres usando a estratégia de busca escalonada padrão,
        delegando a lógica de iteração e validação de domingo para a função externa.
        """
        if not service:
            return {"status": "ERROR", "message": "Erro: Objeto de serviço do Google Calendar não inicializado."}
        return buscar_disponibilidade_escalonada(
            service=service, 
            limite_slots=limite_slots, 
            duracao_minutos=duracao_minutos
        )
    @staticmethod
    def exibir_proximos_horarios_flex(service ,chat_id: str) -> str:
        """
        Tool: Busca 11 slots disponíveis usando a estratégia escalonada (4->10->30 dias).
        Formata e retorna a lista legível para o usuário.
        """
        resultado_tool = ServicesCalendar.buscar_proximos_disponiveis(
            service=service, 
            limite_slots=11, 
            duracao_minutos=60 
        )
        try:
            if resultado_tool.get("status") == "SUCCESS":
                slots_encontrados = resultado_tool.get("available_slots", [])
                
                if not slots_encontrados:
                    return (
                        f"❌ Nossos horários estão lotados nas próximas quatro semanas. "
                        f"Tente novamente em alguns dias."
                    )
                else:            
                    slots_agrupados = {}
                    for slot in slots_encontrados:
                        parts = slot['legivel'].split(' - ')
                        if len(parts) == 2:
                            data_parte = parts[0] 
                            hora_parte = parts[1] 
                            if data_parte not in slots_agrupados:
                                slots_agrupados[data_parte] = []
                            slots_agrupados[data_parte].append(hora_parte)
                    
                    slots_str_agrupado = []
                    for data, horas in slots_agrupados.items():
                        horas_str = ", ".join(horas)
                        slots_str_agrupado.append(f"""Data {data}:
    {horas_str}""")

                    slots_final_output = "\n".join(slots_str_agrupado)

                    return (f"""Encontrei {len(slots_encontrados)} horários disponíveis próximos:
                            
{slots_final_output}

Qual destes horários você gostaria de agendar? (Ex: 'Quero dia 04/12 às 10:00')"""
)
        except Exception as e:
            from services.metrics import registrar_evento
            registrar_evento(
                cliente_id=chat_id,
                event_id='exibir_horario_flex',
                tipo_metrica='agendamento',
                status='error_critico',
                detalhes=f"Falha CRÍTICA no agente date (exibir_proximos_horarios_flex): {str(e)}"
            )
            logger.error(f"Erro CRÍTICO no Agent_date (Groq/Tool-Call): {e}", exc_info=True)
            raise