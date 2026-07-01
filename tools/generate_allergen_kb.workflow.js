export const meta = {
  name: 'generate-allergen-kb',
  description: 'Draft + adversarially verify hidden-ingredient dish KBs for the EU allergens + a meat/animal taxonomy for diets',
  whenToUse: 'Task 10 of the multi-allergen build: seed data/allergen_kb/*.json with verified, safe-by-construction hidden-ingredient priors.',
  phases: [
    { title: 'Draft', detail: 'one agent per allergen drafts hidden-ingredient dishes' },
    { title: 'Verify', detail: 'adversarial pass drops guesses, keeps grounded culinary facts' },
    { title: 'Diet', detail: 'expand the meat/animal ingredient taxonomy' },
  ],
}

// nuts already has a rich hand-built KB (DISH_NUT_KNOWLEDGE) — do NOT regenerate it.
const ALLERGENS = [
  { key: 'milk', display: 'Milk (dairy: butter, cream, cheese, ghee, casein, whey)' },
  { key: 'egg', display: 'Egg (incl. mayonnaise, egg wash, some noodles/batters)' },
  { key: 'soy', display: 'Soy (soy sauce, tofu, edamame, soy lecithin, miso)' },
  { key: 'gluten', display: 'Gluten (cereals containing gluten: wheat, barley, rye, malt)' },
  { key: 'wheat', display: 'Wheat (flour, breading, thickeners, some soy sauces)' },
  { key: 'fish', display: 'Fish (incl. fish sauce, Worcestershire, anchovy, dashi)' },
  { key: 'shellfish', display: 'Shellfish / crustaceans (shrimp, crab, lobster, shrimp paste)' },
  { key: 'mollusc', display: 'Mollusc (oyster sauce, clams, mussels, squid, snails)' },
  { key: 'sesame', display: 'Sesame (tahini, halva, gomashio, some breads/oils)' },
  { key: 'mustard', display: 'Mustard (incl. many dressings, sauces, curries, pickles)' },
  { key: 'celery', display: 'Celery (incl. celeriac, stock/bouillon, mirepoix, spice blends)' },
  { key: 'sulphites', display: 'Sulphites (dried fruit, wine, vinegars, some processed potato)' },
  { key: 'lupin', display: 'Lupin (lupin flour in some breads/pastries, esp. GF/European)' },
]

const KB_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    entries: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          pattern: { type: 'string', description: 'lowercase dish-name substring to match, >=4 chars, distinctive (avoid short ambiguous words)' },
          risk: { type: 'number', description: '0-1 prior probability the dish contains the allergen' },
          note: { type: 'string', description: 'short reason it is a hidden/non-obvious source' },
        },
        required: ['pattern', 'risk', 'note'],
      },
    },
  },
  required: ['entries'],
}

const MEAT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    categories: {
      type: 'object',
      additionalProperties: false,
      properties: {
        meat: { type: 'array', items: { type: 'string' } },
        poultry: { type: 'array', items: { type: 'string' } },
        gelatin: { type: 'array', items: { type: 'string' } },
        honey: { type: 'array', items: { type: 'string' } },
      },
      required: ['meat', 'poultry', 'gelatin', 'honey'],
    },
  },
  required: ['categories'],
}

const draftPrompt = (a) => `You are a culinary allergen expert building a knowledge base for a restaurant allergen-safety app.

Allergen: ${a.display}

List dishes (across WORLD cuisines) where this allergen is commonly present but NOT obvious from the dish name — i.e. a diner scanning a menu would not realize it is there. Focus on HIDDEN sources, not dishes that name the ingredient outright.

Rules:
- 'pattern' = a lowercase substring that appears in the dish NAME (e.g. "pad thai", "tempura", "caesar"). It is matched as a plain substring, so it must be DISTINCTIVE and >=4 characters. Avoid short ambiguous words that collide inside unrelated words (e.g. do NOT use "ham" — it hits "graham"; do NOT use bare "soy" or "egg" as patterns).
- 'risk' = 0-1, how likely a dish matching that name contains the allergen (0.9+ = almost always, 0.6-0.85 = usually/often, below 0.55 = only sometimes — skip anything below 0.5).
- 'note' = short reason (the hidden source).
- Only well-known, real culinary facts. No guesses. If unsure, leave it out.
- 15-30 entries. Cover multiple cuisines.

Return {entries:[{pattern,risk,note}]}.`

const verifyPrompt = (a, draft) => `You are an ADVERSARIAL fact-checker for a food-allergy safety app. Over-including a wrong entry that OVER-warns is acceptable, but a fabricated or misleading culinary claim is not.

Allergen: ${a.display}

Candidate entries (JSON): ${JSON.stringify(draft.entries)}

For EACH candidate decide: is it a REAL, well-known culinary fact that a dish whose name contains this pattern commonly contains ${a.key}? DROP an entry if:
- the claim is a guess, rare, or region-specific enough to be unreliable,
- the pattern is too short/ambiguous or would collide inside unrelated dish names (substring match!) — e.g. reject bare "ham", "ale", "soy", "egg", "oat" as patterns,
- the pattern names the allergen so obviously it is not a HIDDEN source (low value),
- the risk value is overstated.

Keep the good ones, tightening 'pattern' (make it distinctive, lowercase, >=4 chars), adjusting 'risk' to an honest value, and 'note' to a crisp reason. Return only the surviving, corrected entries as {entries:[{pattern,risk,note}]}. It is fine to return fewer.`

phase('Draft')
const kb = await pipeline(
  ALLERGENS,
  (a) => agent(draftPrompt(a), { label: `draft:${a.key}`, phase: 'Draft', schema: KB_SCHEMA, model: 'sonnet' }),
  (draft, a) => agent(verifyPrompt(a, draft), { label: `verify:${a.key}`, phase: 'Verify', schema: KB_SCHEMA, model: 'sonnet' })
    .then((v) => ({ key: a.key, entries: (v && v.entries) || [] })),
)

phase('Diet')
const meatPrompt = `Expand the meat/animal-ingredient taxonomy for a vegetarian/vegan menu checker. These are matched as lowercase substrings against dish NAMES to flag non-vegetarian/non-vegan dishes.

Return {categories:{meat:[...], poultry:[...], gelatin:[...], honey:[...]}} where each list holds distinctive lowercase ingredient/dish-name substrings (>=4 chars where possible; avoid ambiguous short tokens that collide in unrelated words — e.g. no "ham" which hits "graham").
- meat: red/other animal flesh + charcuterie + common meat dish names (beef, pork, lamb, bacon, prosciutto, chorizo, meatball, bolognese, carnitas, ...).
- poultry: chicken/turkey/duck and named poultry dishes.
- gelatin: gelatin-bearing items (gelatin, gelatine, aspic, marshmallow, ...).
- honey: honey and obvious honey-named items.
Comprehensive but precise (no false-friend short tokens). 15-40 per list where the category supports it.`
const meat = await agent(meatPrompt, { label: 'meat-taxonomy', phase: 'Diet', schema: MEAT_SCHEMA, model: 'sonnet' })

return {
  allergens: kb.filter(Boolean),
  meat: (meat && meat.categories) || null,
}
