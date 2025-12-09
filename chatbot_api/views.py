# Arquivo: chatbot_api/views.py (NO PROJETO DJANGO BAAS)

import logging
from django.db import transaction
from django.utils import timezone
from datetime import datetime
from rest_framework.decorators import api_view # Requer Django Rest Framework (DRF)
from rest_framework.response import Response # Requer DRF
from rest_framework import status
from chatbot_api.models import UserRegister # Seus modelos do Django
from django.db import transaction, IntegrityError
from chatbot_api.models import LogMetrica
from chatbot_api.metrics import registrar_evento

logger = logging.getLogger(__name__)

@api_view(['POST'])
@transaction.atomic
def log_metric(request):
    """
    Endpoint HTTP para registrar logs de métrica de forma atômica no PostgreSQL.
    Usado pelo Worker de IA.
    """
    data = request.data
    
    # Validação mínima de payload
    required_fields = ['cliente_id', 'event_id', 'tipo_metrica']
    if not all(field in data for field in required_fields):
        logger.error(f"❌ Tentativa de log de métrica inválida: {data}")
        return Response(
            {"status": "FAILURE", "message": "Campos obrigatórios ausentes."},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        # Chama o Class Method do modelo (que já estava pronto)
        LogMetrica.registrar_evento(
            cliente_id=data.get('cliente_id'),
            event_id=data.get('event_id'),
            tipo_metrica=data.get('tipo_metrica'),
            status=data.get('status', 'success'), # Usa 'success' como default
            detalhes=data.get('detalhes', ''),
        )
        
        # Resposta de sucesso imediata (201 Created)
        return Response({"status": "SUCCESS", "message": "Métrica registrada."}, status=status.HTTP_201_CREATED)
        
    except IntegrityError as e:
        logger.warning(f"⚠️ Erro de Integridade ao registrar métrica (Rollback): {e}")
        return Response({"status": "FAILURE", "message": "Erro de integridade do DB."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    except Exception as e:
        logger.error(f"❌ Erro CRÍTICO ao registrar métrica: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def get_user_data(request, chat_id):
    """
    Endpoint de API para retornar dados de registro de um usuário.
    ✅ AGORA: Implementa o filtro de datas futuras e a formatação (replicando a lógica antiga).
    """
    try:
        user = UserRegister.objects.get(chat_id=chat_id)
        
        consultas = []
        agora = timezone.now() # Data e hora atuais (com timezone)

        # --- Lógica de Filtro e Formatação (Replicada do código antigo) ---
        
        # SLOTS 1 e 2
        
        # 🟢 Slot 1
        if user.appointment1_gcal_id and user.appointment1_datetime and user.appointment1_datetime >= agora:
            local_dt1 = timezone.localtime(user.appointment1_datetime)
            
            consultas.append({
                "appointment_number": 1,
                # 🎯 CRÍTICO: Formatação da data e hora para o Agente ler
                "data": local_dt1.strftime("%d/%m/%Y"),
                "hora": local_dt1.strftime("%H:%M"),
                "slot": 1,
                "gcal_id": user.appointment1_gcal_id, # CRÍTICO: Necessário para o cancelamento
                "datetime_iso": user.appointment1_datetime.isoformat()
            })

        # 🟢 Slot 2
        if user.appointment2_gcal_id and user.appointment2_datetime and user.appointment2_datetime >= agora:
            local_dt2 = timezone.localtime(user.appointment2_datetime)

            consultas.append({
                "appointment_number": 2,
                # 🎯 CRÍTICO: Formatação da data e hora para o Agente ler
                "data": local_dt2.strftime("%d/%m/%Y"),
                "hora": local_dt2.strftime("%H:%M"),
                "slot": 2,
                "gcal_id": user.appointment2_gcal_id, # CRÍTICO: Necessário para o cancelamento
                "datetime_iso": user.appointment2_datetime.isoformat()
            })

        # 🟢 Ordenação (Replicada da lógica antiga)
        consultas.sort(key=lambda x: datetime.strptime(f"{x['data']} {x['hora']}", "%d/%m/%Y %H:%M"))
        
        # --- Estrutura da Resposta ---
        response_data = {
            "status": "SUCCESS",
            "chat_id": user.chat_id,
            "username": user.username,
            "appointments": consultas # ✅ Retorna a lista completa de consultas ativas e formatadas
        }
        
        return Response(response_data, status=status.HTTP_200_OK)
        
    except UserRegister.DoesNotExist:
        return Response({"status": "NOT_FOUND", "message": "Usuário não registrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
                        
    except Exception as e:
        logger.error(f"❌ Erro ao buscar dados de usuário no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@transaction.atomic
def salvar_agendamento_transacional(request):
    """
    Endpoint de API que recebe a requisição do Worker de IA e executa a 
    sua lógica de slots e transação atômica.
    """
    
    chat_id = request.data.get('chat_id')
    google_event_id = request.data.get('google_event_id')
    start_time_iso = request.data.get('start_time_iso')
    
    if not all([chat_id, google_event_id, start_time_iso]):
        return Response({"status": "ERROR", "message": "Parâmetros incompletos."}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # SUA LÓGICA DE NEGÓCIO COMPLETA (PRESENTE AQUI!)
        # O UserRegister.objects.select_for_update() garante o lock de linha
        # A transação (transaction.atomic) está garantida pelo decorator @transaction.atomic, 
        # mas você também pode envolvê-la explicitamente aqui se preferir:
        
        # O bloco abaixo é o que estava no seu antigo criar_agendamento_db:
        
        user = UserRegister.objects.select_for_update().get(chat_id=chat_id) 
        
        new_datetime = datetime.fromisoformat(start_time_iso)
        agora = timezone.now()
        
        is_slot1_free = not user.appointment1_gcal_id or (user.appointment1_datetime and user.appointment1_datetime < agora)
        
        if is_slot1_free:
            user.appointment1_datetime = new_datetime
            user.appointment1_gcal_id = google_event_id
            user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
            
            logger.info(f"✅ Agendamento salvo no slot 1 (BaaS) - Cliente: {chat_id}")
            
            # Retorna o JSON de sucesso para o Worker de IA
            response_data = {"status": "SUCCESS", "slot": 1, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
            return Response(response_data, status=status.HTTP_200_OK)

        is_slot2_free = not user.appointment2_gcal_id or (user.appointment2_datetime and user.appointment2_datetime < agora)
        
        if is_slot2_free:
            user.appointment2_datetime = new_datetime
            user.appointment2_gcal_id = google_event_id
            user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id'])
            
            logger.info(f"✅ Agendamento salvo no slot 2 (BaaS) - Cliente: {chat_id}")
            
            # Retorna o JSON de sucesso para o Worker de IA
            response_data = {"status": "SUCCESS", "slot": 2, "data": new_datetime.strftime('%d/%m/%Y às %H:%M')}
            return Response(response_data, status=status.HTTP_200_OK)
        else:
            # Retorna o JSON de falha para o Worker de IA
            return Response({"status": "FAILURE", "message": "Limite de agendamentos atingido. Você pode ter no máximo 2 consultas ativas."}, 
                            status=status.HTTP_409_CONFLICT) # 409 Conflict é adequado
                            
    except UserRegister.DoesNotExist:
        # Retorna a falha para o Worker de IA
        return Response({"status": "FAILURE", "message": "Usuário não registrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
        
    except Exception as e:
        logger.error(f"❌ Erro grave ao salvar agendamento no BaaS: {e}")
        # Retorna erro de servidor para o Worker de IA
        return Response({"status": "ERROR", "message": "Ocorreu um erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

# Seu projeto Django: chatbot_api/views.py (ADICIONAR/COMPLEMENTAR)

# ... (Imports existentes: logging, transaction, api_view, Response, status, UserRegister) ...

@api_view(['POST']) 
def register_user(request):
    """
    Endpoint para criar um novo usuário.
    O worker faz um POST aqui para registrar o nome fornecido.
    """
    try:
        # A transação atômica garante que, se o registro falhar, 
        # o banco de dados não seja alterado.
        with transaction.atomic():
            data = request.data
            chat_id = data.get('chat_id')
            username = data.get('name') 
            
            if not chat_id or not username:
                return Response({"status": "FAILURE", "message": "Campos 'chat_id' e 'name' são obrigatórios."}, 
                                status=status.HTTP_400_BAD_REQUEST)
            
            # Tenta criar o usuário (chat_id é unique)
            user = UserRegister.objects.create(chat_id=chat_id, username=username)
            
            # O Worker espera uma resposta com o nome do usuário para a mensagem final
            return Response({
                "status": "SUCCESS", 
                "message": "Usuário registrado com sucesso.", 
                "username": user.username 
            }, status=status.HTTP_201_CREATED)
            
    except IntegrityError:
        # Se o chat_id for UNIQUE e já existir, retorna 409 Conflict
        return Response({"status": "FAILURE", "message": "Usuário já existe."}, 
                        status=status.HTTP_409_CONFLICT) 
        
    except Exception as e:
        logger.error(f"❌ Erro ao registrar usuário: {e}", exc_info=True)
        return Response({"status": "ERROR", "message": "Erro interno do servidor."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def cancel_appointment_transacional(request):
    """
    Endpoint de API para limpar o slot de agendamento no DB (limpa o slot).
    Garante a atomicidade e o lock de linha.
    """
    chat_id = request.data.get('chat_id')
    numero_consulta = request.data.get('numero_consulta')

    if not all([chat_id, numero_consulta]):
        return Response({"status": "ERROR", "message": "Parâmetros incompletos."}, 
                        status=status.HTTP_400_BAD_REQUEST)
    
    try:
        numero_consulta = int(numero_consulta)
    except ValueError:
         return Response({"status": "ERROR", "message": "numero_consulta deve ser um inteiro."}, 
                        status=status.HTTP_400_BAD_REQUEST)

    try:
        with transaction.atomic():
            # CRÍTICO: Lock de linha para exclusão
            user = UserRegister.objects.select_for_update().get(chat_id=chat_id)
            
            db_slot_cleared = False
            
            if numero_consulta == 1 and user.appointment1_gcal_id:
                user.appointment1_datetime = None
                user.appointment1_gcal_id = None
                user.save(update_fields=['appointment1_datetime', 'appointment1_gcal_id'])
                db_slot_cleared = True
                
            elif numero_consulta == 2 and user.appointment2_gcal_id:
                user.appointment2_datetime = None
                user.appointment2_gcal_id = None
                user.save(update_fields=['appointment2_datetime', 'appointment2_gcal_id'])
                db_slot_cleared = True
                
            if not db_slot_cleared:
                return Response({"status": "FAILURE", "message": f"Não encontrei agendamento ativo no slot {numero_consulta} para limpar."}, 
                                status=status.HTTP_404_NOT_FOUND)

            logger.info(f"✅ Slot {numero_consulta} LIMPO no DB (BaaS) - Cliente: {chat_id}")
            
            return Response({"status": "SUCCESS", "message": "Slot limpo no banco de dados."}, 
                            status=status.HTTP_200_OK)

    except UserRegister.DoesNotExist:
        return Response({"status": "FAILURE", "message": "Usuário não encontrado."}, 
                        status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"❌ Erro grave ao limpar slot {numero_consulta} no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Ocorreu um erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['GET'])
def list_active_appointments(request, chat_id):
    """
    Endpoint de API para retornar uma lista formatada de agendamentos ATIVOS.
    A lógica de 'ativo' e a formatação são executadas AQUI no BaaS.
    """
    try:
        user = UserRegister.objects.get(chat_id=chat_id)
        lista_consultas = []
        agora = timezone.now() # Usa a timezone configurada no Django

        # Lógica de negócio: Slot 1
        if user.appointment1_datetime and user.appointment1_datetime > agora:
            lista_consultas.append({
                "appointment_number": 1, 
                "data": user.appointment1_datetime.strftime('%d/%m/%Y'),
                "hora": user.appointment1_datetime.strftime('%H:%M'),
                "gcal_id": user.appointment1_gcal_id,
                "datetime_iso": user.appointment1_datetime.isoformat()
            })

        # Lógica de negócio: Slot 2
        if user.appointment2_datetime and user.appointment2_datetime > agora:
            lista_consultas.append({
                "appointment_number": 2, 
                "data": user.appointment2_datetime.strftime('%d/%m/%Y'),
                "hora": user.appointment2_datetime.strftime('%H:%M'),
                "gcal_id": user.appointment2_gcal_id,
                "datetime_iso": user.appointment2_datetime.isoformat()
            })

        return Response({"status": "SUCCESS", "appointments": lista_consultas}, status=status.HTTP_200_OK)
        
    except UserRegister.DoesNotExist:
        return Response({"status": "NOT_FOUND", "appointments": []}, status=status.HTTP_404_NOT_FOUND)
                        
    except Exception as e:
        logger.error(f"❌ Erro ao listar agendamentos ativos no BaaS: {e}")
        return Response({"status": "ERROR", "message": "Erro interno no BaaS."}, 
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
