# GSM8K Benchmark — glm-5.3-flash

- **Run ID**: `2026-09-03T13-00-46.406210Z_3535be81`
- **Date**: `2026-09-03T13:00:46.406246+00:00`
- **Mode**: gsm8k
- **Label**: <code>glm53-gsm8k</code>
- **Accuracy**: **94.0%**
- **Completion**: 100.0%
- **Rating**: ★★★★★ Excellent

## GSM8K — Grade School Math

- **Accuracy**: **94.0%** (188/200)
- **Rating**: ★★★★★ Excellent
- **Few-shot examples**: 8-shot CoT
- **Dataset**: openai/gsm8k test (1319 total, 200 evaluated)
- **Duration**: 488.7s
- **Tokens consumed**: 165,805

### Answer Extraction Methods

| Method | Count |
|---|---:|
| marker | 200 |

### Error Analysis

- **Total failures**: 12 / 200
- **Wrong answer**: 12 — model produced a numeric answer that didn't match the ground truth

### Failed Questions (12 total)

| # | Question (excerpt) | Ground Truth | Extracted | Method | Response (excerpt) |
|---:|---|---:|---:|---|---|
| 12 | Carlos is planting a lemon tree. The tree will cost $90 to plant. Each year it will grow 7 lemons, which he can sell … | 13.0 | 12.0 | marker | Each year the tree produces 7 lemons, which sell for 7 * 1.5 = 10.5 dollars. After paying 3 dollars to water and feed the tree, he earns 10.5 - 3 = 7.5 dollars per year. To cover the 90 dollar cost… |
| 25 | Marie ordered one chicken meal that costs $12, 5 packs of milk that costs $3 each, 4 apples that cost $1.50 each, and… | 2.0 | 1.0 | marker | Chicken meal: $12. Milk: 5 × $3 = $15. Apples: 4 × $1.50 = $6. So far: 12 + 15 + 6 = $42. Money left for pizza: 50 - 42 = $8. Each pizza box costs $8.50, so Marie ordered 8 ÷ 8.50 ≈ 1 box. #### 1 |
| 37 | John plans to sell all his toys and use the money to buy video games. He has 13 lego sets and he sells them for $15 e… | 2.0 | 0.0 | marker | John sold all 13 lego sets for $15 each, earning 13 * 15 = $195. He bought 8 video games for $20 each, costing 8 * 20 = $160, and had $5 left. Since he sold all his lego sets, he has 0 left. #### 0 |
| 60 | A basket contains 25 oranges among which 1 is bad, 20% are unripe, 2 are sour and the rest are good. How many oranges… | 17.0 | 13.0 | marker | There are 25 oranges total. Bad: 1. Unripe: 20% of 25 = 5. Sour: 2. So good oranges = 25 - 1 - 5 - 2 = 13. #### 13 |
| 119 | Adrien's total salary was 30 percent higher than Lylah's. Four years later, his salary had increased, and he was earn… | 95200.0 | 96000.0 | marker | Adrien earned $40000 four years ago, which was 30% higher than Lylah's salary. So Lylah's salary was 40000 / 1.3 = $30769.23.  Four years later, Adrien's salary increased by 40%: 40000 * 1.4 = $560… |
| 128 | Well's mother sells watermelons, peppers, and oranges at the local store. A watermelon costs three times what each pe… | 880.0 | 680.0 | marker | Each pepper costs $15. A watermelon costs 3 times that: 3 * 15 = $45. An orange costs 5 less than a watermelon: 45 - 5 = $40.  - 4 watermelons: 4 * 45 = $180 - 20 peppers: 20 * 15 = $300 - 10 orang… |
| 139 | In a candy machine, there are 22 more than four times the number of pink gumballs as there are blue gumballs. If ther… | 70.0 | 60.0 | marker | There are 12 blue gumballs. Four times that is 4 * 12 = 48. There are 22 more than that, so 48 + 22 = 60. #### 60 |
| 146 | Johnny is picking up the toys on the floor of his room.  He'd dumped a lego boxed set with 500 pieces on the floor, a… | 2125.0 | 2000.0 | marker | The first set has 500 pieces. The second has 3 times more: 3 * 500 = 1500. The third has 1/4 the number: 500 / 4 = 125. Total: 500 + 1500 + 125 = 2000. #### 2000 |
| 165 | For his 30th birthday, Elvira chose a new computer with many accessories as a gift. She has a budget of €1500 donated… | 77.0 | 179.0 | marker | The computer costs €1090, the scanner €157, the CD burner €74, and the printer €102. Total spent: 1090 + 157 + 74 + 102 = 1321. From her budget of €1500, she has 1500 - 1321 = 179 left. #### 179 |
| 182 | Jean is two years older than Mark.  Two years ago Mark was 5 years older than half Jan's age.  If Jan is 30 how old i… | 23.0 | 32.0 | marker | Jean is two years older than Mark, so Mark is 30 - 2 = 28. (Checking: two years ago Mark was 26, and half of Jan's age then was 15... the condition is consistent with Jean being Jan's age plus 2.) … |
| 186 | Russell works at a pet store and is distributing straw among the rodents. The rats are kept in 3 cages in equal group… | 5.0 | 30.0 | marker | The hamsters get 10 cages * 5 pieces = 50 pieces of straw. The rabbits get 20 pieces. So hamsters and rabbits use 50 + 20 = 70 pieces. The rats get 160 - 70 = 90 pieces. With 3 cages of equal group… |
| 187 | Mandy owes Benedict $100. They agreed to have monthly interest of 2%. If Mandy was able to pay it after 3 months, how… | 106.0 | 106.12 | marker | Mandy owes $100 with 2% monthly interest. Each month the amount grows by 2%: after 1 month, 100 * 1.02 = 102. After 2 months, 102 * 1.02 = 104.04. After 3 months, 104.04 * 1.02 = 106.1208. #### 106.12 |

### Detailed Failure Samples

#### Question #12

**Expected:** 13.0 · **Got:** 12.0 · **Method:** marker

**Question:**

> Carlos is planting a lemon tree. The tree will cost $90 to plant. Each year it will grow 7 lemons, which he can sell for $1.5 each. It costs $3 a year to water and feed the tree. How many years will i

**Model response:**

```
Each year the tree produces 7 lemons, which sell for 7 * 1.5 = 10.5 dollars. After paying 3 dollars to water and feed the tree, he earns 10.5 - 3 = 7.5 dollars per year. To cover the 90 dollar cost: 90 / 7.5 = 12.
#### 12
```

#### Question #25

**Expected:** 2.0 · **Got:** 1.0 · **Method:** marker

**Question:**

> Marie ordered one chicken meal that costs $12, 5 packs of milk that costs $3 each, 4 apples that cost $1.50 each, and some boxes of pizza. Marie paid a total of $50. How many boxes of pizza did Marie

**Model response:**

```
Chicken meal: $12. Milk: 5 × $3 = $15. Apples: 4 × $1.50 = $6. So far: 12 + 15 + 6 = $42. Money left for pizza: 50 - 42 = $8. Each pizza box costs $8.50, so Marie ordered 8 ÷ 8.50 ≈ 1 box.
#### 1
```

#### Question #37

**Expected:** 2.0 · **Got:** 0.0 · **Method:** marker

**Question:**

> John plans to sell all his toys and use the money to buy video games. He has 13 lego sets and he sells them for $15 each. He ends up buying 8 video games for $20 each and has $5 left. How many lego se

**Model response:**

```
John sold all 13 lego sets for $15 each, earning 13 * 15 = $195. He bought 8 video games for $20 each, costing 8 * 20 = $160, and had $5 left. Since he sold all his lego sets, he has 0 left.
#### 0
```

#### Question #60

**Expected:** 17.0 · **Got:** 13.0 · **Method:** marker

**Question:**

> A basket contains 25 oranges among which 1 is bad, 20% are unripe, 2 are sour and the rest are good. How many oranges are good?

**Model response:**

```
There are 25 oranges total. Bad: 1. Unripe: 20% of 25 = 5. Sour: 2. So good oranges = 25 - 1 - 5 - 2 = 13.
#### 13
```

#### Question #119

**Expected:** 95200.0 · **Got:** 96000.0 · **Method:** marker

**Question:**

> Adrien's total salary was 30 percent higher than Lylah's. Four years later, his salary had increased, and he was earning 40% more than what he was making four years ago. If Adrien's and Lylah's salary

**Model response:**

```
Adrien earned $40000 four years ago, which was 30% higher than Lylah's salary. So Lylah's salary was 40000 / 1.3 = $30769.23.

Four years later, Adrien's salary increased by 40%: 40000 * 1.4 = $56000.

Since their salaries increased simultaneously, Lylah's also increased by 40%: 30769.23 * 1.4 = $40000.

Total salary: 56000 + 40000 = 96000.
#### 96000
```
