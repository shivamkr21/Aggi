import pdfplumber
import uuid
from datetime import datetime
from pdfminer.pdfinterp import PDFPageInterpreter

#Start - For invalid color operator warning supression
def _safe_color_op(func):
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except ValueError:
            return None
    return wrapper

# Patch known non-stroking color operators if present
for op in ("do_rg", "do_g", "do_k", "do_sc", "do_scn"):
    if hasattr(PDFPageInterpreter, op):
        setattr(
            PDFPageInterpreter,
            op,
            _safe_color_op(getattr(PDFPageInterpreter, op))
        )

#End - For invalid color operator warning supression

def generate_Id(types):
    return f"{types}_{uuid.uuid1().hex[:17]}"

def get_Time():
    now = datetime.now()
    now = str(now)
    lst = now.split('.')
    now = lst[0]

    now = datetime.strptime(now, "%Y-%m-%d %H:%M:%S")
    val = now.strftime("%d-%m-%Y %H:%M:%S")

    print(val)
    #print("")
    return now

def extract_blocks(pdf_path):
    blocks = []
    error_trigger = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(use_text_flow=True, extra_attrs=["fontname", "size"])
            
            #print(page_no)
            for w in words:
                #if(new_txt == 0):
                #    new_txt = 1

                blocks.append({
                    "text": w["text"],   #Text
                    "font_height": round(w["height"], 2),   #Text Height
                    "font_width": round(w["width"], 2),   #Text Width
                    "font_name": w["fontname"],   # Text Font
                    "page_no": page_no,
                    "x0": round(w["x0"], 4),   #Text Left x cordinate
                    "x1": round(w["x1"], 4),   #Text Right x cordinate
                    "top": round(w["top"], 4),   #Distance from top of page to top of word
                    "bottom": round(w["bottom"], 4),   #Distance from top of page to bottom of word
                    "upright": w["upright"],   #If written horizontal or not
                    "direction": w["direction"]   #Text Direction LTR RTL TTB BTT
                })

    return blocks

def main(PDF_Name):
    
    print("Reading PDF...")
    start_Time = get_Time()
    print("")
    t1 = start_Time
    
    blocks = extract_blocks(PDF_Name)
    
    print("PDF Retrived")
    t2 = get_Time()
    print("Time: " + str(t2-t1))
    print("")
    t1 = t2
    
    str_vec = []
    chapter_vec = []
    
    print("Opening File and Extracting Text")
    get_Time()
    print("")
    
    output_file = "Chapter_1_Chunk.py"
    with open(output_file, "w", encoding="utf-8") as f:

        font_size = 0
        text_str = ""
        checker = 0

        chapter_chk = "False"

        topic_iden = "False"

        
        for i in blocks:

            #Checking neecessary text
            #SideText_Check
            upright_val = str(i["upright"])
            font_val = str(i["font_name"])
            height_val = str(i["font_height"])
            part_val = "False"

            #Part_Check
            if(font_val == "ZWSAVT+ArnoPro-BoldCaption" and height_val == "18.0"):
                part_val = "True"

            #ChapterNum_Check
            if(font_val == "YOIUHV+GaramondPremrPro-Smbd" and height_val == "31.0"):
                part_val = "True"


            #Required Text Only 
            if(upright_val == "True" and part_val == "False"):

                txt_val = str(i["text"])
                width_val = str(i["font_width"])
                page_val = str(i["page_no"])
                x0_val = str(i["x0"])
                x1_val = str(i["x1"])
                top_val = str(i["top"])
                bottom_val = str(i["bottom"])
                direction_val = str(i["direction"])
            
                if(checker == 1):
                    f.write("\n")
                f.write("{")
                f.write("\n")
                f.write("   'text': '" + txt_val + "', ")
                f.write("'page_no': " + page_val + ", ")
                
                f.write("'font': '" + font_val + "', ")
                f.write("'font_size': (" + height_val + ", " + width_val + "), ")
                
                f.write("'x0': " + x0_val + ", ")
                f.write("'x1': " + x1_val + ", ")
                
                f.write("'top': "+ top_val + ", ")
                #f.write("'bottom': "+ bottom_val + ", ")
                
                #f.write("'upright': "+ upright_val + ", ")
                #f.write("'direction': "+ direction_val)
                
                f.write("\n")
                f.write("},")

                if(font_size != height_val):
                    if(checker == 0):
                        #Chapter Identifier
                        if(font_val == "YOIUHV+GaramondPremrPro-Smbd" and height_val == "17.0"):
                            chapter_chk = "True"
                        checker = 1
                    else:
                        str_vec.append(text_str)

                        if(chapter_chk == "True"):
                            chapter_chk = "False"
                            ids = generate_Id("CH")
                            chapter_vec.append(text_str + " - " + ids)

                        #Chapter Identifier
                        if(font_val == "YOIUHV+GaramondPremrPro-Smbd" and height_val == "17.0"):
                            chapter_chk = "True"

                    text_str = txt_val
                    font_size = height_val

                else:
                    text_str = text_str + " " + txt_val


                
                #Topic Identifier
                #if(font_val == "GVJUPF+ZapfDingbatsStd" && txt_val == "■" && height_val == "10.0"):
                #    topic_iden = "True"
                #if(font_val == "ZWSAVT+ArnoPro-BoldCaption" && height_val == "11.0" && topic_iden == "True"):
                #Sub-Topic Identifier
                #elif(font_val == "ZWSAVT+ArnoPro-BoldCaption" && height_val == "11.0"):

                #if(font_size != height_val):
                #    if(checker == 0):
                #        checker = 1
                #    else:
                #        str_vec.append(text_str)
                        
                #    text_str = txt_val
                #    font_size = height_val
                    
                #else:
                #    text_str = text_str + " " + txt_val

    print(f"Text written to {output_file}")
    t2 = get_Time()
    print("Time: " + str(t2-t1))
    print("")
    t1 = t2

    print("Writing Chunk to file")
    get_Time()
    print("")

    str_output_file = "Chapter_1_Text.py"

    with open(str_output_file, "w", encoding="utf-8") as f:
        for i in str_vec:
            f.write(i)
            f.write("\n")

    chp_output_file = "Chapter_Name.txt"

    with open(chp_output_file, "w", encoding="utf-8") as f:
        for i in chapter_vec:
            f.write(i)
            f.write("\n")

    print(f"Chunk written to {str_output_file}")
    t2 = get_Time()
    print("Time: " + str(t2-t1))
    print("")
    end_Time = t2
    
    time_Taken = end_Time - start_Time
    time_Taken = str(time_Taken)
    time_Taken = time_Taken[0:7]

    print("Total Time: ", time_Taken)

if __name__ == "__main__":
    main("./PDF/Split/Harrison 2022 mobile edition-42-130.pdf")