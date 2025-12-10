package main

import (
	"context"
	"io"
	"log"
	"net/http"
	"os"
	"time" // Necessário para o timeout do Redis assíncrono

	"go_waha_gateway/services/hmac"
	"go_waha_gateway/services/redis"
)

// Define o contexto de longa duração para o Redis Client
var ctx = context.Background()

// Limite de 1 Megabyte (1024 * 1024 bytes) - Segurança contra Payloads Grandes
const MAX_BODY_SIZE int64 = 1048576 

func main() {
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

// Handler Principal do Webhook
func webhookHandler(w http.ResponseWriter, r *http.Request) {
	// Rejeita qualquer coisa que não seja POST
	if r.Method != http.MethodPost {
		http.Error(w, "Método não permitido", http.StatusMethodNotAllowed)
		return
	}
	
	// CRÍTICO: DEFER IMEDIATO. Garante que r.Body.Close() será chamado.
	defer r.Body.Close()

	// PASSO 1: LER o corpo da requisição BRUTO (RAW BODY) com limite
    limitedReader := io.LimitReader(r.Body, MAX_BODY_SIZE)
	rawBody, err := io.ReadAll(limitedReader)

	// Trata erro de leitura ou limite excedido
	if err != nil {
		log.Printf("❌ Erro ao ler body da requisição: %v", err)
		http.Error(w, "Erro ao ler body", http.StatusInternalServerError)
		return
	}
    
    // VERIFICAÇÃO DE LIMITE EM PROFUNDIDADE (Fallback)
    if r.ContentLength > MAX_BODY_SIZE || (r.ContentLength == -1 && len(rawBody) == int(MAX_BODY_SIZE)) {
        log.Println("❌ Requisição recusada: Tamanho do corpo excedeu o limite máximo (1MB).")
        http.Error(w, "Payload Too Large", http.StatusRequestEntityTooLarge)
        return
    }

	// PASSO 2: Validação HMAC (SEGURANÇA EXTREMA)
	hmacHeader := r.Header.Get("X-Webhook-Hmac")

	if hmacHeader == "" || !hmac.ValidateHmac(rawBody, hmacHeader) {
		log.Println("❌ Requisição recusada: HMAC ausente ou inválido.")
		http.Error(w, "Forbidden: Invalid HMAC signature", http.StatusForbidden)
		return
	}

	// PASSO 3: Responde HTTP 200 OK IMEDIATAMENTE
	// Se chegou até aqui, a requisição é válida. Responda imediatamente para latência mínima.
	w.WriteHeader(http.StatusOK)
	log.Println("✨ Webhook processado com sucesso! Resposta HTTP 200 enviada.")

	// PASSO 4: PUBLICAR no Redis - AGORA ASSÍNCRONO COM GOROUTINE
	// Delega o I/O de rede do Redis para uma rotina em background.
	go func(payload []byte) {
        // Usa um contexto de curta duração para o Redis para evitar bloqueio infinito (Timeout).
        // Não usamos r.Context() pois a goroutine principal já retornou (a requisição HTTP terminou).
		ctxRedis, cancel := context.WithTimeout(context.Background(), 5*time.Second) 
		defer cancel() // Libera o recurso do Context

		if err := redis.PublishMessage(ctxRedis, payload); err != nil {
			// Apenas loga o erro. O cliente já recebeu o 200 OK.
			log.Printf("❌ ERRO ASSÍNCRONO CRÍTICO: Falha ao publicar no Redis: %v", err)
		} else {
            // Log de sucesso opcional (remova para alto volume)
			log.Printf("✅ Publicação no Redis FINALIZADA em BACKGROUND.")
		}
	}(rawBody) // Passa o payload lido para a Goroutine

	// O handler retorna imediatamente após iniciar a goroutine.
}