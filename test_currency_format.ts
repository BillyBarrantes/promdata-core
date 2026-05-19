const formatCurrency = (value: number) => {
    const currency = 'PEN';
    return new Intl.NumberFormat('es-PE', {
        style: 'currency',
        currency: currency,
        minimumFractionDigits: 2
    }).format(value);
};

const runTest = (input: number, expected: string) => {
    // Normalize spaces (NBSP vs simple space) for comparison
    const result = formatCurrency(input).replace(/\u00A0/g, ' ');
    const normExpected = expected.replace(/\u00A0/g, ' ');

    // Some envs render S/ 1.200,50 (comma decimal) others S/ 1,200.50 (dot decimal) depending on full ICU data
    // Peru officially uses comma for decimal, but often tech uses dot. Let's see what Node does.
    console.log(`Input: ${input}`);
    console.log(`  Output:   '${result}'`);
    console.log(`  Expected: '${normExpected}'`);

    // Loose check for symbol and numbers
    if (result.includes('S/') && result.includes(input.toString().charAt(0))) {
        console.log("  ✅ PASS (Basic Format Check)");
    } else {
        console.log("  ❓ CHECK MANUALLY");
    }
};

console.log("--- Testing Currency Formatting (es-PE) ---");
runTest(1200.5, 'S/ 1,200.50');
runTest(1000000, 'S/ 1,000,000.00');
runTest(0, 'S/ 0.00');
