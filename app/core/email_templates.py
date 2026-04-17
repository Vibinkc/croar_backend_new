import datetime


def wrap_in_celebratory_template(content_html: str, title: str = "Congratulations!") -> str:
    """
    Wraps HTML content in a premium, celebratory email template.
    Includes a confetti/celebration feel via CSS and colors.
    """
    current_year = datetime.datetime.now().year

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                margin: 0;
                padding: 0;
                background-color: #f4f7f6;
                color: #333;
            }}
            .container {{
                max-width: 600px;
                margin: 20px auto;
                background-color: #ffffff;
                border-radius: 12px;
                overflow: hidden;
                box-shadow: 0 4px 15px rgba(0,0,0,0.1);
            }}
            .header {{
                background: linear-gradient(135deg, #6e8efb, #a777e3);
                padding: 40px 20px;
                text-align: center;
                color: white;
                position: relative;
            }}
            .header h1 {{
                margin: 0;
                font-size: 28px;
                letter-spacing: 1px;
                text-shadow: 0 2px 4px rgba(0,0,0,0.2);
            }}
            .celebration-icon {{
                font-size: 50px;
                margin-bottom: 10px;
                display: block;
            }}
            .content {{
                padding: 40px;
                line-height: 1.6;
                font-size: 16px;
            }}
            .footer {{
                background-color: #f9f9f9;
                padding: 20px;
                text-align: center;
                font-size: 12px;
                color: #888;
                border-top: 1px solid #eeeeee;
            }}
            .button {{
                display: inline-block;
                padding: 14px 30px;
                background-color: #6e8efb;
                color: #ffffff !important;
                text-decoration: none;
                border-radius: 8px;
                font-weight: bold;
                margin: 20px 0;
                box-shadow: 0 4px 6px rgba(110, 142, 251, 0.3);
            }}
            .confetti {{
                position: absolute;
                top: 0;
                left: 0;
                width: 100%;
                height: 100%;
                z-index: 0;
                opacity: 0.3;
                pointer-events: none;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>{title}</h1>
            </div>
            <div class="content">
                {content_html}
            </div>
            <div class="footer">
                <p>&copy; {current_year} Our Company. All rights reserved.</p>
                <p>Ensuring a smooth transition for our new team members.</p>
            </div>
        </div>
    </body>
    </html>
    """
