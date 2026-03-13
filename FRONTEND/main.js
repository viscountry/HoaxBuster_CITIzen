AOS.init({
  duration: 1200,
  once: true,
  easing: "ease-out-cubic",
});

feather.replace();

function toggleMobileMenu() {
  const menu = document.getElementById("mobile-menu");
  if (menu) menu.classList.toggle("hidden");
}

/* close mobile menu */

document.addEventListener("click", function (e) {
  const menu = document.getElementById("mobile-menu");
  const btn = document.querySelector('[onclick="toggleMobileMenu()"]');

  if (!menu || !btn) return;

  if (!menu.contains(e.target) && !btn.contains(e.target)) {
    menu.classList.add("hidden");
  }
});

/* WORD COUNTER */

function updateWordCount() {
  const textarea = document.getElementById("claim-input");
  const counter = document.getElementById("word-count");

  if (!textarea || !counter) return;

  const words = textarea.value
    .trim()
    .split(/\s+/)
    .filter((w) => w.length > 0);

  counter.textContent = `${words.length}/1500 words`;
}

/* CLEAR BUTTON */

document.addEventListener("DOMContentLoaded", () => {
  const clearBtn = document.getElementById("clear-button");

  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      const textarea = document.getElementById("claim-input");
      const results = document.getElementById("results-section");

      textarea.value = "";

      if (results) {
        results.classList.add("hidden");
      }

      updateWordCount();
    });
  }
});

/* FLASHCARD QUIZ */

document.addEventListener("DOMContentLoaded", () => {
  const startBtn = document.getElementById("startButton");

  if (!startBtn) return;

  startBtn.addEventListener("click", () => {
    const container = document.getElementById("flashcardContainer");

    container.classList.remove("hidden");

    const flashcards = [
      {
        question: "What misinformation spread during the 2025 LA fires?",
        answer: "False rumors claiming it was a government cover-up.",
      },
      {
        question: "What did the FBI warn about election videos?",
        answer: "Fake election videos linked to Russian disinformation.",
      },
      {
        question: "How is fake news spread online?",
        answer: "By impersonating real news websites.",
      },
      {
        question: "How can you check fake news?",
        answer: "Search other trusted sources.",
      },
      {
        question: "Why compare multiple sources?",
        answer: "To verify accuracy and detect bias.",
      },
    ];

    let index = 0;

    container.innerHTML = `
<div class="flashcard-container">

<div class="flashcard" id="card">

<div class="flashcard-front" id="front"></div>

<div class="flashcard-back" id="back"></div>

</div>

</div>

<div style="text-align:center;margin-top:20px">

<button id="prev">Prev</button>
<button id="flip">Flip</button>
<button id="next">Next</button>

</div>

`;

    const card = document.getElementById("card");
    const front = document.getElementById("front");
    const back = document.getElementById("back");

    function update() {
      front.textContent = flashcards[index].question;
      back.textContent = flashcards[index].answer;
      card.classList.remove("flipped");
    }

    update();

    document.getElementById("flip").onclick = () => {
      card.classList.toggle("flipped");
    };

    document.getElementById("next").onclick = () => {
      if (index < flashcards.length - 1) {
        index++;
        update();
      }
    };

    document.getElementById("prev").onclick = () => {
      if (index > 0) {
        index--;
        update();
      }
    };
  });
});
