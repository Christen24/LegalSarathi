import asyncio
from playwright.async_api import async_playwright
import os

HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
<style>
    body { font-family: 'Arial', sans-serif; padding: 50px; line-height: 1.6; color: #333; background: #fff; width: 800px; }
    .header { text-align: center; border-bottom: 2px solid #333; padding-bottom: 20px; margin-bottom: 30px; }
    .header h1 { margin: 0; font-size: 24pt; color: #c00; }
    .header p { margin: 5px 0; font-size: 10pt; color: #666; }
    .meta { display: flex; justify-content: space-between; margin-bottom: 30px; }
    .address-block { margin-bottom: 30px; }
    .subject { font-weight: bold; text-decoration: underline; margin-bottom: 20px; }
    .content p { margin-bottom: 15px; text-align: justify; }
    .footer { margin-top: 50px; }
    .signature { width: 200px; border-top: 1px solid #333; margin-top: 40px; padding-top: 5px; }
</style>
</head>
<body>
    <div class="header">
        <h1>LEGAL DEMAND NOTICE</h1>
        <p>RELIANCE LEGAL ASSOCIATES | Advocates & Legal Consultants</p>
        <p>12th Floor, Nariman Point, Mumbai - 400021</p>
    </div>

    <div class="meta">
        <div>Ref No: RLA/2024/782</div>
        <div>Date: April 27, 2026</div>
    </div>

    <div class="address-block">
        To,<br>
        <b>Mr. Akash Sharma</b>,<br>
        Proprietor, Sharma Electronics,<br>
        Sector 18, Noida, UP - 201301.
    </div>

    <div class="subject">
        Subject: FINAL DEMAND NOTICE FOR UNPAID DUES OF INR 4,14,750/-
    </div>

    <div class="content">
        <p>Dear Sir,</p>
        <p>Under instructions from our client, <b>M/s Global Tech Solutions</b>, we hereby serve you with this formal legal notice regarding your outstanding balance.</p>
        <p>1. You had placed orders for electronic components via Invoice No. GTS/991 dated January 15, 2024, for a total sum of <b>INR 4,14,750</b> (Four Lakhs Fourteen Thousand Seven Hundred and Fifty Only).</p>
        <p>2. Despite multiple reminders and a grace period of 90 days, you have failed to clear the said dues, which constitutes a <b>Breach of Contract</b> under the Indian Contract Act, 1872.</p>
        <p>3. You are hereby called upon to pay the total sum of INR 4,14,750 along with 18% interest per annum within <b>15 days</b> of receipt of this notice.</p>
        <p>Failure to comply will leave our client with no option but to initiate civil recovery proceedings and criminal action under Section 138 of the NI Act if applicable.</p>
    </div>

    <div class="footer">
        Yours faithfully,<br>
        For Reliance Legal Associates
        <div class="signature">
            Senior Advocate<br>
            (Seal & Signature)
        </div>
    </div>
</body>
</html>
"""

async def generate_image():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.set_content(HTML_CONTENT)
        # Set viewport to wrap content
        await page.set_viewport_size({"width": 900, "height": 1100})
        
        output_path = os.path.abspath("valid_legal_notice.png")
        await page.screenshot(path=output_path, full_page=True)
        await browser.close()
        print(f"IMAGE_PATH:{output_path}")

if __name__ == "__main__":
    asyncio.run(generate_image())
