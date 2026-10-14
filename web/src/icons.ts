// Icons for chores, grouped for the picker. Any emoji can still be typed in;
// these are the ones a household reaches for.
export const ICON_GROUPS: { name: string; icons: { icon: string; label: string }[] }[] = [
  { name: "Pets", icons: [
    { icon: "🐕", label: "dog" }, { icon: "🦮", label: "dog walk" }, { icon: "🐈", label: "cat" },
    { icon: "🐟", label: "fish" }, { icon: "🐹", label: "hamster" }, { icon: "🐦", label: "bird" },
    { icon: "🦴", label: "bone" }, { icon: "🐾", label: "paws" }, { icon: "💩", label: "poop" } ] },
  { name: "Kitchen", icons: [
    { icon: "🍽️", label: "dishes" }, { icon: "🧽", label: "sponge" }, { icon: "🍳", label: "cook" },
    { icon: "🥪", label: "lunches" }, { icon: "🛒", label: "groceries" }, { icon: "🧊", label: "fridge" },
    { icon: "☕", label: "coffee" }, { icon: "🫖", label: "kettle" } ] },
  { name: "Cleaning", icons: [
    { icon: "🧹", label: "sweep" }, { icon: "🧼", label: "soap" }, { icon: "🪣", label: "mop" },
    { icon: "🧴", label: "spray" }, { icon: "🚽", label: "toilet" }, { icon: "🛁", label: "bath" },
    { icon: "🚿", label: "shower" }, { icon: "🪟", label: "windows" }, { icon: "🛏️", label: "bed" },
    { icon: "🧸", label: "toys" } ] },
  { name: "Laundry", icons: [
    { icon: "🧺", label: "laundry" }, { icon: "👕", label: "shirt" }, { icon: "🧦", label: "socks" },
    { icon: "👖", label: "jeans" }, { icon: "🫧", label: "wash" } ] },
  { name: "Trash", icons: [
    { icon: "🗑️", label: "bins" }, { icon: "♻️", label: "recycling" }, { icon: "📦", label: "boxes" } ] },
  { name: "Outside", icons: [
    { icon: "🌱", label: "plants" }, { icon: "🪴", label: "houseplant" }, { icon: "🌿", label: "weeding" },
    { icon: "🍂", label: "leaves" }, { icon: "❄️", label: "snow" }, { icon: "🚗", label: "car" },
    { icon: "🚲", label: "bike" }, { icon: "📬", label: "mail" } ] },
  { name: "Other", icons: [
    { icon: "📚", label: "homework" }, { icon: "🎹", label: "practice" }, { icon: "💊", label: "medicine" },
    { icon: "💡", label: "lights" }, { icon: "🔧", label: "fix" }, { icon: "🧾", label: "bills" },
    { icon: "⭐", label: "star" }, { icon: "✅", label: "check" } ] },
];
