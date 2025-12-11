# =========================================================================
# 1. FUNÇÕES DE CONTROLE (Comuns a Vários Agentes)
# =========================================================================

FINALIZAR_USER_SCHEMA = {
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
}


# =========================================================================
# 2. SCHEMA DEDICADO - AGENTE DE REGISTRO (agent_register.py)
# =========================================================================

REGISTRATION_TOOL_SCHEMA = {
    "type": "function", 
    "function": {
        "name": "enviar_dados_user",
        "description": "Registra um novo usuário no banco de dados com seu ID de chat e nome. Use esta ferramenta APENAS se o usuário pedir para se cadastrar e fornecer seu nome VERDADEIRO **NUNCA USE PLACEHOLDERS**.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "O ID único do chat/usuário do WhatsApp. Essencial para o registro."
                },
                "name": {
                    "type": "string",
                    "description": "O nome fornecido pelo usuário para o registro na conversa."
                }
            },
            "required": ["chat_id", "name"] 
        }
    }
}


# =========================================================================
# 3. SCHEMAS DEDICADOS - AGENTE DE AGENDAMENTO (agent_date.py)
# =========================================================================

EXIBIR_HORARIOS_FLEX_SCHEMA = {
    "type": "function",
    "function": {
        "name": "exibir_proximos_horarios_flex",
        "description": "Ferramenta CRÍTICA. Deve ser chamada imediatamente após 'ver_horarios_disponiveis' retornar com sucesso para processar e formatar a lista de slots disponíveis para o usuário.",
        "parameters": {
            "type": "object",
            "properties": {} # O Groq não precisa saber os detalhes internos. A função opera no estado.
        }
    }
}

BUSCAR_HORARIOS_DISPONIVEIS_SCHEMA = {
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
}

CONFIRMAR_AGENDAMENTO_SCHEMA = {
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

# -------------------------------------------------------------------------
# CONSOLIDAÇÃO DO AGENTE DE DATA (Para importação nos Agentes)
# -------------------------------------------------------------------------

TOOLS_DATE_SEARCH = [
    FINALIZAR_USER_SCHEMA, 
    BUSCAR_HORARIOS_DISPONIVEIS_SCHEMA,
    EXIBIR_HORARIOS_FLEX_SCHEMA,
]

TOOLS_DATE_CONFIRM = [
    FINALIZAR_USER_SCHEMA, 
    CONFIRMAR_AGENDAMENTO_SCHEMA
]


# =========================================================================
# 4. SCHEMA DEDICADO - AGENTE DE CANCELAMENTO (agent_consul_cancel.py)
# =========================================================================

CANCELAR_CONSULTA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "cancelar_consulta",
        "description": "Cancela a consulta do usuário com base no número do agendamento (slot). O slot 1 é a primeira consulta, o 2 é a segunda.",
        "parameters": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "O ID único do chat/usuário do WhatsApp."
                },
                "numero_consulta": {
                    "type": "integer",
                    "description": "O número da consulta a ser cancelada (1 ou 2)."
                }
            },
            "required": ["chat_id", "numero_consulta"]
        }
    }
}

# -------------------------------------------------------------------------
# CONSOLIDAÇÃO DO AGENTE DE CANCELAMENTO
# -------------------------------------------------------------------------

TOOLS_CANCEL = [
    FINALIZAR_USER_SCHEMA, 
    CANCELAR_CONSULTA_SCHEMA
]