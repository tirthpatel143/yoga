// DOM Elements
const chatHistory = document.getElementById('chat-history');
const userInput = document.getElementById('user-input');
const sendButton = document.getElementById('send-button');

// Mock Product Data (For Demonstration)
const mockProducts = [
    {
        title: "Zafu Maharaja - Almofada pufe",
        price: "R$ 230,00",
        image: "https://medusa-yogateria-staging.s3.sa-east-1.amazonaws.com/zafu-maharaja-ameixa.jpg",
        url: "https://yogateria.com.br/produto/zafu-maharaja-almofada-pufe/"
    },
    {
        title: "Tapete de Yoga Premium",
        price: "R$ 450,00",
        image: "https://medusa-yogateria-staging.s3.sa-east-1.amazonaws.com/yogateria-almofada-zafu-maharaja-bege_1.png",
        url: "https://yogateria.com.br/"
    },
    {
        title: "Bloco de Yoga em Cortiça",
        price: "R$ 89,00",
        image: "https://medusa-yogateria-staging.s3.sa-east-1.amazonaws.com/yogateria-almofada-zafu-maharaja-preto_1.png",
        url: "https://yogateria.com.br/"
    }
];

// Helper: Create a message element
function createMessage(content, role = 'user') {
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'bubble';
    bubble.innerText = content;

    messageDiv.appendChild(bubble);
    return messageDiv;
}

// Helper: Create product card
function createProductCard(product) {
    const card = document.createElement('div');
    card.className = 'product-card';

    card.innerHTML = `
        <img src="${product.image}" alt="${product.title}" class="product-image">
        <div class="product-info">
            <h3 class="product-title">${product.title}</h3>
            <span class="product-price">${product.price}</span>
            <a href="${product.url}" target="_blank" class="view-btn">View Product</a>
        </div>
    `;

    return card;
}

// Function to handle sending message
async function sendMessage() {
    const text = userInput.value.trim();
    if (!text) return;

    // 1. Add User Message
    chatHistory.appendChild(createMessage(text, 'user'));
    userInput.value = '';

    // Scroll to bottom
    scrollToBottom();

    // 2. AI Thinking State
    const thinkingId = 'thinking-' + Date.now();
    const aiMsg = createMessage('Thinking...', 'ai');
    aiMsg.id = thinkingId;
    chatHistory.appendChild(aiMsg);
    scrollToBottom();

    try {
        // 3. Real API Call to Localhost
        const response = await fetch('http://localhost:8000/chat', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ message: text })
        });

        if (!response.ok) throw new Error('Server issues');

        const data = await response.json();

        // Update thinking message
        const bubble = aiMsg.querySelector('.bubble');

        // 4. Show Product Cards if available - BEFORE answer is shown (and visually above)
        if (data.products && data.products.length > 0) {
            const productsContainer = document.createElement('div');
            productsContainer.className = 'products-container';

            // Insert cards BEFORE the text bubble
            aiMsg.insertBefore(productsContainer, bubble);

            // Add cards one by one with a small delay
            for (const product of data.products) {
                const card = createProductCard(product);
                productsContainer.appendChild(card);
                scrollToBottom();
                await new Promise(r => setTimeout(r, 200));
            }
        }

        // Render Markdown Response directly
        // Using marked.parse() ensures proper HTML rendering (including tables) and preserves spaces
        bubble.innerHTML = marked.parse(data.response);

    } catch (error) {
        console.error('Error:', error);
        aiMsg.querySelector('.bubble').innerText = "I'm having trouble connecting to the server. Please make sure the backend is running at localhost:8000.";
    }

    scrollToBottom();
}

function scrollToBottom() {
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// Event Listeners
sendButton.addEventListener('click', sendMessage);

userInput.addEventListener('keypress', (e) => {
    if (e.key === 'Enter') {
        sendMessage();
    }
});

// Auto-focus input
window.addEventListener('load', () => {
    userInput.focus();
});
