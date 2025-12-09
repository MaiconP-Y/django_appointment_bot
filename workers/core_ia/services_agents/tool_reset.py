REROUTE_COMPLETED_STATUS = "REROUTE_COMPLETED"
# 🎯 NOVO SINAL GLOBAL para informar que o Agent deve fazer o I/O de reset.
RESET_SIGNAL = "__USER_WANTS_RESET__" 

def finalizar_user(history_str: str) -> str:
    """
    FUNÇÃO PURA: Apenas extrai a última mensagem do usuário e retorna um sinal
    para o Agent. REMOVE I/O e a chamada complexa ao roteador.
    """
    
    # ❌ AS CHAMADAS delete_session_state(chat_id) e delete_history(chat_id) FORAM REMOVIDAS
    # E SERÃO MOVIMENTADAS PARA O AGENT!
    
    history_lines = history_str.split('\n')
    last_user_message_content = "menu" # Default se não encontrar
    
    for line in reversed(history_lines):
        if line.startswith("[User]:"):
            # Extrai o conteúdo exato da última mensagem para re-roteamento
            last_user_message_content = line.replace("[User]:", "", 1).strip()
            break
            
    # Retorna o sinal de reset e a mensagem do usuário que será usada para re-roteamento
    return f"{RESET_SIGNAL}|{last_user_message_content}"