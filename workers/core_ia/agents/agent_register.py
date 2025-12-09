import os
import json 
from groq import Groq

from core_ia.services_agents.prompts_agents import prompt_register
from services.redis_client import delete_history, delete_session_state
from core_ia.services_agents.tool_reset import REROUTE_COMPLETED_STATUS
from core_api.django_api_service import DjangoApiService
from services.metrics import registrar_evento
groq_service = Groq()

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
def api_register_user_tool(chat_id: str, name: str) -> dict:
    """
    Função Helper para ser chamada pelo Agent (LLM).
    Delega a lógica de registro ao DjangoApiService.
    """
    
    payload = {"chat_id": chat_id, "name": name}
    response = DjangoApiService.register_user(payload)
    if response and response.get('status') == 'SUCCESS':
        # Nota: Ajustei tipo_metrica para 'cadastro', se não estiver mapeado em models.py, 
        # use o tipo_metrica mais próximo, como 'agendamento'
        registrar_evento(
            cliente_id=chat_id,
            event_id=f"registro_{chat_id}",
            tipo_metrica='agendamento', 
            status='success',
            detalhes=f"Novo usuário registrado: {name}"
        )
    
    # Retorna o JSON de resposta para o LLM.
    return response

class Agent_register():
    """
    Classe de serviço dedicada a interagir com a API da Groq, usando o histórico completo (history_str)
    para manter o contexto e delegar ações de registro via Tool Calling.
    """
    def __init__(self):
        try:
            self.client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
        except Exception as e:
            raise EnvironmentError("A variável GROQ_API_KEY não está configurada.") from e
    
    def generate_register(self, history_str: str, chat_id: str) -> str:
        """
        Gera uma resposta da IA, usando a string do histórico completo como a última mensagem do usuário.
        
        :param history_str: O histórico completo da conversa como uma string (User: ... \n Assistant: ...).
        :return: A string de resposta gerada pela IA.
        """
        
        mensagens = [
            {
                "role": "system",
                "content": prompt_register,
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
                tools=[REGISTRATION_TOOL_SCHEMA],
                tool_choice="auto",
                temperature=0.1 , 
            )

            response_message = chat_completion.choices[0].message
            resposta_ia = response_message.content
            
            if response_message.tool_calls:
                available_functions = {
                    "enviar_dados_user": api_register_user_tool, 
                }
                
                mensagens.append(response_message)
                
                for tool_call in response_message.tool_calls:
                    function_name = tool_call.function.name
                    function_to_call = available_functions[function_name]
                    
                    function_args = json.loads(tool_call.function.arguments)
                    
                    function_args['chat_id'] = chat_id 
                    
                    registration_result = function_to_call(**function_args) 
                    if (registration_result and 
                        isinstance(registration_result, dict) and 
                        registration_result.get('username')): # ✅ Usa .get() para verificar a chave no dicionário
                        
                        nome_usuario = registration_result['username'] # ✅ Acessa como chave de dicionário
                        delete_history(chat_id)
                        delete_session_state(chat_id)
                        
                        return (f"""{REROUTE_COMPLETED_STATUS}|Cadastro realizado com sucesso! 
Seja bem vindo {nome_usuario}! Como posso te ajudar hoje?"""
                        )
                    else:
                        tool_content = "FALHA: Usuário já existe ou erro no banco de dados. Informe o usuário."                   
                    
                    mensagens.append(
                        {
                            "tool_call_id": tool_call.id,
                            "role": "tool", 
                            "name": function_name,
                            "content": f"Resultado do registro de usuário: {tool_content}"
                        }
                    )
                    
                final_completion = self.client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=mensagens 
                )
            
                return final_completion.choices[0].message.content
            
            return resposta_ia
            
        except Exception as e:
            print(f"Erro ao chamar a API da Groq: {e}")
            return "Desculpe, estou tendo problemas técnicos para responder agora."