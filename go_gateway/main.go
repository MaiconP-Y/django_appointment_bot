package main

import (
	"context"
	"io"
	"log"
	"net/http"
	"os"
	"time" 

	"go_waha_gateway/services/hmac"
	"go_waha_gateway/services/redis"
)

// Define o contexto de longa duração para o Redis Client
var ctx = context.Background()

// Limite de 1 Megabyte (1024 * 1024 bytes) - Segurança contra Payloads Grandes
const MAX_BODY_SIZE int64 = 1048576 

func main() {
    // ... (Código existente de inicialização do HMAC e Redis) ...
	// 1. Inicializa o Serviço HMAC
	if err := hmac.InitSecret(); err != nil {
		log.Fatalf("❌ Falha crítica ao carregar a chave HMAC: %v", err)
	}

	// 2. Inicializa o Serviço Redis (com teste de conexão)
	if err := redis.InitClient(ctx); err != nil {
		log.Fatalf("❌ Falha crítica ao inicializar o Redis: %v", err)
	}
	log.Println("✅ Conexão Redis estabelecida com sucesso!")

	// 3. Configuração do Servidor HTTP
	http.HandleFunc("/webhook", webhookHandler)

	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	log.Printf("🚀 Gateway Go INICIADO na porta :%s", port)

	if err := http.ListenAndServe(":"+port, nil); err != nil {
		log.Fatalf("❌ Erro fatal ao iniciar o servidor: %v", err)
	}
}


func webhookHandler(w http.ResponseWriter, r *http.Request) {
    
    // PASSO 1: Leitura do Body e limite
	r.Body = http.MaxBytesReader(w, r.Body, MAX_BODY_SIZE)
	rawBody, err := io.ReadAll(r.Body)
	if err != nil {
		log.Printf("❌ Erro ao ler body do request ou limite excedido: %v", err)
		// Envia um status 400 se o corpo for inválido ou muito grande
		http.Error(w, "Bad Request: Invalid body or size limit exceeded", http.StatusBadRequest) 
		return
	}
    
    // PASSO 2: Validação HMAC (SEGURANÇA EXTREMA)
	hmacHeader := r.Header.Get("X-Webhook-Hmac")

	if hmacHeader == "" || !hmac.ValidateHmac(rawBody, hmacHeader) {
		log.Println("❌ Requisição recusada: HMAC ausente ou inválido.")
		http.Error(w, "Forbidden: Invalid HMAC signature", http.StatusForbidden)
		return
	}

	// PASSO 3: Responde HTTP 200 OK IMEDIATAMENTE (SUCESSO DA LATÊNCIA BAIXA)
	w.WriteHeader(http.StatusOK)
	log.Println("✨ Webhook processado com sucesso! Resposta HTTP 200 enviada.")

	// =========================================================================
	// === PASSO 4: PUBLICAR no Redis - AGORA COM CHECK DE IDEMPOTÊNCIA ===
	// =========================================================================
	go func(payload []byte) {
		// Contexto de curta duração para o Redis (5s)
		ctxRedis, cancel := context.WithTimeout(context.Background(), 5*time.Second) 
		defer cancel() 

        // 4.1. Tentar extrair o ID (NOVO)
		eventID, err := redis.ExtractEventID(payload)

		if err != nil {
            // Se falhar a extração do ID (ex: notificação de status/leitura), 
            // logamos e pulamos a checagem de duplicata.
			log.Printf("⚠️ ID de evento não encontrado/inválido. Publicando sem checagem de duplicata: %v", err)
            // Continua para o passo 4.3 (Publicação)
		} else {
            // 4.2. Checar e Registrar Idempotência no Redis (NOVO)
			isDuplicate, err := redis.CheckAndSetIdempotency(ctxRedis, eventID)

			if err != nil {
                // Erro de Redis (falha de infra). Publicamos para não perder a mensagem.
				log.Printf("❌ ERRO Redis CRÍTICO (Idempotência). Publicando Evento: %v", err)
			} else if isDuplicate {
                // 4.2.1: DUPLICATA ENCONTRADA E DESCARTADA (O CORAÇÃO DA OTIMIZAÇÃO)
				log.Printf("❌ DUPLICATA DESCARTADA pelo Gateway Go. ID: %s", eventID)
				return // **SAI DA GOROUTINE.** O payload NÃO é publicado.
			}
            log.Printf("✅ Evento ÚNICO aceito pelo Gateway. ID: %s", eventID)
            // Continua para o passo 4.3 (Publicação)
		}

		// 4.3: Publicação na Fila (Somente se não for descartado)
		if err := redis.PublishMessage(ctxRedis, payload); err != nil {
			log.Printf("❌ ERRO ASSÍNCRONO CRÍTICO: Falha ao publicar no Redis: %v", err)
		}
	}(rawBody)
}