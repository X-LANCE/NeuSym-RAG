/* database emnlp_papers: This database contains documents published on the EMNLP conference as well as their referenced papers publicly available on the Arxiv website. Each PDF file is represented or parsed via different views, e.g., pages, sections, figures, tables, and references. We also extract the concrete content inside each concrete element via OCR.
*/
/* table metadata: This table stores metadata about each paper, including the number of pages, paper path and paper id.
*/
CREATE TABLE IF NOT EXISTS metadata (
	paper_id UUID, -- A unique identifier for this paper.
	title VARCHAR, -- The title of this paper.
	abstract VARCHAR, -- The abstract of this paper.
	num_pages INTEGER, -- The number of pages in this paper.
	conference_full VARCHAR, -- The full name of the conference where this paper was published. `uncategorized` if the paper has not been published yet.
	conference_abbreviation VARCHAR, -- The abbreviation of the conference where this paper was published. `uncategorized` if the paper has not been published yet.
	pub_year INTEGER, -- The year when this paper was published.
	volume VARCHAR, -- The volume of the conference where this paper was published. e.g. findings.
	download_url VARCHAR, -- The url from which the paper can be downloaded.
	bibtex VARCHAR, -- The bibtex of this paper.
	authors VARCHAR[], -- The full names of authors of this paper.
	pdf_path VARCHAR, -- The path to the PDF file of this paper.
	tldr VARCHAR, -- A brief summary of the paper's main idea or findings generated by LLM based on title and abstract.
	tags VARCHAR[], -- Keywords representing the paper's topics, methods, or applications generated by LLM based on title and abstract.,
	PRIMARY KEY (paper_id)
);
/* table pages: This table stores information of the pages in the papers, including their content, size and their order within the paper. The pagesare extracted using PyMuPDF.
*/
CREATE TABLE IF NOT EXISTS pages (
	page_id UUID, -- A unique identifier for each page.
	page_number INTEGER, -- The page number in the current paper, starting from 1.
	page_width INTEGER, -- The pixel width of the current page.
	page_height INTEGER, -- The pixel height of the current page.
	page_content VARCHAR, -- The content of the page.
	page_summary VARCHAR, -- A brief summary of the page content, generated by LLM, focusing on key information and describing the page content.
	ref_paper_id UUID, -- A foreign key linking to the `paper_id` in the `metadata` table.,
	PRIMARY KEY (page_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id)
);
/* table images: This table stores the information about images in each paper. The images are extracted using MinerU.
*/
CREATE TABLE IF NOT EXISTS images (
	image_id UUID, -- The unique identifier of the image.
	image_caption VARCHAR, -- The caption of this image, "" if it doesn't have one.
	image_summary VARCHAR, -- A brief summary of the image, generated by LLM, focusing on key information and describing the image.
	bounding_box INTEGER[4], -- The bounding box of the figure in the format [x0, y0, w, h], where (x0, y0) represents the coordinates of the top-left corner and (w, h) represents the width and height which are used to determine the shape of the rectangle.
	ordinal INTEGER, -- Each figure is labeled with one distinct integer number in the current page, which starts from 0.
	ref_paper_id UUID, -- A foreign key linking to the `paper_id` in the `metadata` table.
	ref_page_id UUID, -- A foreign key linking to the `page_id` in the `pages` table.,
	PRIMARY KEY (image_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id),
	FOREIGN KEY (ref_page_id) REFERENCES pages(page_id)
);
/* table chunks: This table contains the information of each chunk of text (chunk size = 512 tokens with no overlapping) in each page of the paper. A chunk is a sub-text that is extracted from the main text using langchain, such as a sentence or a paragraph.
*/
CREATE TABLE IF NOT EXISTS chunks (
	chunk_id UUID, -- A unique identifier for each chunk of text.
	text_content VARCHAR, -- The text content of the current chunk.
	ordinal INTEGER, -- Each chunk is labeled with one distinct integer number in the current page, which starts from 0.
	ref_paper_id UUID, -- A foreign key linking to the `paper_id` in the `metadata` table.
	ref_page_id UUID, -- A foreign key linking to the `page_id` in the `pages` table.,
	PRIMARY KEY (chunk_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id),
	FOREIGN KEY (ref_page_id) REFERENCES pages(page_id)
);
/* table tables: This table stores information about tables extracted from pages using MinerU, including content (in html format), bounding box and summary of each table generated by LLM.
*/
CREATE TABLE IF NOT EXISTS tables (
	table_id UUID, -- A unique identifier for each table.
	table_caption VARCHAR, -- Caption of the table, showing key information of the table.
	table_content VARCHAR, -- The content of the table in html format.
	table_summary VARCHAR, -- A brief summary of the table content generated by LLM, focusing on key information and describing the table content.
	bounding_box INTEGER[4], -- The bounding box of the table in the format [x0, y0, w, h], where (x0, y0) represents the coordinates of the top-left corner and (w, h) represents the width and height.
	ordinal INTEGER, -- Each table is labeled with one distinct integer number in the current page, which starts from 0.
	ref_paper_id UUID, -- A foreign key linking to the `paper_id` in the `metadata` table.
	ref_page_id UUID, -- A foreign key linking to the `page_id` in the `pages` table where this table is located.,
	PRIMARY KEY (table_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id),
	FOREIGN KEY (ref_page_id) REFERENCES pages(page_id)
);
/* table sections: This table contains the text content of each section in the paper, e.g. `Introduction`, `Experiment`, `Conclusion`, `Reference`. The sections are extracted using PyMuPDF or MinerU.
*/
CREATE TABLE IF NOT EXISTS sections (
	section_id UUID, -- A unique identifier for each section of text.
	section_title VARCHAR, -- The title of the current section.
	section_content VARCHAR, -- The text content of the current section.
	section_summary VARCHAR, -- A brief summary of the section content generated by LLM, focusing on key information and describing the section content.
	ordinal INTEGER, -- Each section is labeled with one distinct integer number in the paper, which starts from 0.
	page_numbers INTEGER[], -- A list of page numbers where current section is located in the relevant paper.
	ref_paper_id UUID, -- A foreign key linking to the `paper_id` in the `metadata` table.,
	PRIMARY KEY (section_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id)
);
/* table equations: This table stores information about equations extracted from pages using MinerU.
*/
CREATE TABLE IF NOT EXISTS equations (
	equation_id UUID, -- A unique identifier for each equation.
	equation_content VARCHAR, -- Content of the equation in latex format.
	ordinal INTEGER, -- Each equation is labeled with one distinct integer number in the current page, which starts from 0.
	ref_paper_id UUID, -- A foreign key linking to the paper ID in the `metadata` table.
	ref_page_id UUID, -- A foreign key linking to the page ID in the `pages` table where this equation is located.,
	PRIMARY KEY (equation_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id),
	FOREIGN KEY (ref_page_id) REFERENCES pages(page_id)
);
/* table reference: This table stores information about references extracted using MinerU.
*/
CREATE TABLE IF NOT EXISTS reference (
	reference_id UUID, -- A unique identifier for each reference.
	reference_content VARCHAR, -- Content of the reference.
	ordinal INTEGER, -- Each reference is labeled with one distinct integer number according to its order in the paper, which starts from 0.
	ref_paper_id UUID, -- A foreign key linking to the `paper_id` in the `metadata` table.
	ref_page_id UUID, -- A foreign key linking to the `page_id` in the `pages` table where this reference is located.,
	PRIMARY KEY (reference_id),
	FOREIGN KEY (ref_paper_id) REFERENCES metadata(paper_id),
	FOREIGN KEY (ref_page_id) REFERENCES pages(page_id)
);