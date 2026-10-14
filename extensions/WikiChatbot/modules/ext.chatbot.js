(function () {
    // Sử dụng $(function() {}) để đảm bảo code chạy ngay khi script được tải xong
    $(function () {
        var toggler = document.getElementById("chatbot-toggler");
        var container = document.getElementById("chatbot-container");
        var closeBtn = document.getElementById("close-chat");
        var sendBtn = document.getElementById("send-btn");
        var input = document.getElementById("chat-input");
        var messages = document.getElementById("chat-messages");

        // Hàm bật tắt khung chat
        function toggleChat() {
            if (!container) return;
            if (container.style.display === "none" || container.style.display === "") {
                container.style.display = "flex";
                // Focus vào ô nhập liệu ngay khi mở
                if(input) input.focus();
            } else {
                container.style.display = "none";
            }
        }

        // Gán sự kiện click (kiểm tra kỹ null để tránh lỗi)
        if (toggler) {
            toggler.addEventListener("click", function(e) {
                e.preventDefault(); // Ngăn chặn hành vi mặc định nếu có
                toggleChat();
            });
        }
        
        if (closeBtn) {
            closeBtn.addEventListener("click", toggleChat);
        }

        function sendMessage() {
            if (!input || !messages) return;
            var text = input.value.trim();
            if (!text) return;

            // 1. Hiện tin nhắn User
            var userDiv = document.createElement("div");
            userDiv.className = "user-msg";
            userDiv.textContent = text;
            messages.appendChild(userDiv);
            input.value = "";
            messages.scrollTop = messages.scrollHeight;

            // 2. Hiệu ứng đang nhập
            var loadingDiv = document.createElement("div");
            loadingDiv.className = "bot-msg";
            loadingDiv.style.fontStyle = "italic";
            loadingDiv.textContent = "Đang suy nghĩ...";
            messages.appendChild(loadingDiv);
            messages.scrollTop = messages.scrollHeight;

            // 3. Gọi API
            var apiUrl = mw.config.get("WikiChatbotApiUrl") || "http://localhost:8000/ask";
            var controller = new AbortController();
            var settled = false;
            var timer = setTimeout(function () {
                controller.abort();
                finish("Yêu cầu đã hết thời gian. Vui lòng thử lại.");
            }, 30000);

            function finish(answer) {
                if (settled) return;
                settled = true;
                clearTimeout(timer);
                if (!loadingDiv.parentNode) return;
                var botDiv = document.createElement("div");
                botDiv.className = "bot-msg";
                botDiv.textContent = answer;
                loadingDiv.replaceWith(botDiv);
                messages.scrollTop = messages.scrollHeight;
            }

            fetch(apiUrl, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ query: text }),
                signal: controller.signal
            })
            .then(function (response) {
                if (!response.ok) throw new Error("request");
                return response.json();
            })
            .then(data => {
                if (data && !Array.isArray(data) && typeof data.answer === "string" && data.answer.trim()) {
                    finish(data.answer);
                } else {
                    finish("Không nhận được phản hồi hợp lệ.");
                }
            })
            .catch(function () {
                finish("Không nhận được phản hồi. Vui lòng thử lại.");
            });
        }

        if (sendBtn) sendBtn.addEventListener("click", sendMessage);
        if (input) {
            input.addEventListener("keydown", function(e) {
                if (e.key === "Enter" && !e.isComposing) {
                    e.preventDefault();
                    sendMessage();
                }
            });
        }
    });
}());
