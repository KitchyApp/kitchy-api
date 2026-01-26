async function sendImage() {
    const fileInput = document.getElementById("imageInput");
    const status = document.getElementById("status");
    const ingredientsList = document.getElementById("ingredients");
    const recipesDiv = document.getElementById("recipes");

    ingredientsList.innerHTML = "";
    recipesDiv.innerHTML = "";
    status.innerText = "⏳ A analisar imagem...";

    if (!fileInput.files.length) {
        status.innerText = "❌ Seleciona uma imagem primeiro";
        return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    try {
        const response = await fetch(
            "http://127.0.0.1:8000/analyze-image/?language=pt",
            {
                method: "POST",
                body: formData
            }
        );

        const data = await response.json();

        // ⚠️ RATE LIMIT → fallback
        if (data.error && data.error.toLowerCase().includes("rate")) {
            status.innerText = "⚠️ Muitas análises seguidas. A mostrar sugestões.";

            const fallback = getFallbackRecipes();
            renderResults(fallback);

            localStorage.setItem("lastResult", JSON.stringify(fallback));
            return;
        }

        // ❌ outro erro
        if (data.error) {
            status.innerText = "❌ " + (data.details || data.error);
            return;
        }

        status.innerText = "✅ Pronto!";
        renderResults(data);

        localStorage.setItem("lastResult", JSON.stringify(data));

    } catch (err) {
        // 🔄 fallback se houver erro total
        const cached = localStorage.getItem("lastResult");
        if (cached) {
            status.innerText = "⚠️ Offline. A mostrar último resultado.";
            renderResults(JSON.parse(cached));
        } else {
            status.innerText = "❌ Erro ao comunicar com o servidor";
        }
    }
}

/* -------- helpers -------- */

function renderResults(data) {
    const ingredientsList = document.getElementById("ingredients");
    const recipesDiv = document.getElementById("recipes");

    ingredientsList.innerHTML = "";
    recipesDiv.innerHTML = "";

    data.ingredients_detected.forEach(i => {
        const li = document.createElement("li");
        li.innerText = i;
        ingredientsList.appendChild(li);
    });

    data.recipes.forEach(r => {
        const div = document.createElement("div");
        div.innerHTML = `
            <h3>${r.title} (${r.time_minutes} min)</h3>
            <ol>${r.steps.map(s => `<li>${s}</li>`).join("")}</ol>
        `;
        recipesDiv.appendChild(div);
    });
}

function getFallbackRecipes() {
    return {
        ingredients_detected: ["tomate", "alho", "azeite"],
        recipes: [
            {
                title: "Tomate Salteado Simples",
                time_minutes: 10,
                steps: [
                    "Corta os tomates em pedaços.",
                    "Aquece o azeite numa frigideira.",
                    "Junta o alho e depois o tomate.",
                    "Salteia 5 minutos e serve."
                ]
            },
            {
                title: "Molho Rápido de Tomate",
                time_minutes: 15,
                steps: [
                    "Refoga o alho em azeite.",
                    "Adiciona tomate picado.",
                    "Cozinha até reduzir.",
                    "Usa como molho simples."
                ]
            }
        ]
    };
}